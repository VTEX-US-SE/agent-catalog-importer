"""LLM-based SKU selector assessment and planning utilities."""
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..utils.logger import get_agent_logger


class SKUSelectorAssessor:
    """Builds selector plans by category using OpenAI or Anthropic."""

    def __init__(self):
        self.logger = get_agent_logger("sku_selector_assessor")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

    def _extract_product_attributes(self, product: Dict[str, Any]) -> Dict[str, List[str]]:
        attributes: Dict[str, List[str]] = {}
        # Product-level specifications (supports both keys)
        product_specs = (
            product.get("specifications", [])
            or product.get("Specifications", [])
            or []
        )
        for spec in product_specs:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("Name", "")).strip()
            value = str(spec.get("Value", "")).strip()
            if not name:
                continue
            if name not in attributes:
                attributes[name] = []
            if value and value not in attributes[name]:
                attributes[name].append(value)

        # SKU-level specifications are usually the source for selector attributes
        for sku in product.get("skus", []) or []:
            sku_specs = (
                sku.get("specifications", [])
                or sku.get("Specifications", [])
                or []
            )
            for spec in sku_specs:
                if not isinstance(spec, dict):
                    continue
                name = str(spec.get("Name", "")).strip()
                value = str(spec.get("Value", "")).strip()
                if not name:
                    continue
                if name not in attributes:
                    attributes[name] = []
                if value and value not in attributes[name]:
                    attributes[name].append(value)
        return attributes

    def build_category_attribute_map(
        self,
        products: List[Dict[str, Any]],
        category_resolver,
    ) -> Dict[int, Dict[str, Any]]:
        """Aggregate attribute names/values by resolved VTEX category ID."""
        by_category: Dict[int, Dict[str, Any]] = {}
        for product in products:
            category_id = category_resolver(product)
            if not category_id:
                continue

            leaf_category_name = ""
            categories = product.get("categories", []) or []
            if categories:
                leaf_category_name = str(categories[-1].get("Name", "")).strip()

            item = by_category.setdefault(
                int(category_id),
                {
                    "category_id": int(category_id),
                    "category_name": leaf_category_name or f"Category {category_id}",
                    "attributes": {},
                },
            )
            for attr_name, values in self._extract_product_attributes(product).items():
                bucket = item["attributes"].setdefault(attr_name, [])
                for value in values:
                    if value not in bucket:
                        bucket.append(value)
        return by_category

    def _extract_json(self, text: str) -> Dict[str, Any]:
        cleaned = text.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
        return json.loads(cleaned)

    def _ask_openai(self, attributes: Dict[str, List[str]]) -> Dict[str, Any]:
        prompt = (
            "From these attributes, identify the SINGLE most important attribute that should "
            "act as the SKU selector (Radio button) on the Home/Product page. "
            "All other attributes should be informational Product Specifications (Text).\n\n"
            f"Attributes: {json.dumps(attributes, ensure_ascii=False)}\n\n"
            'Return ONLY JSON with: {"selector_attribute":"...", "reason":"..."}'
        )
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.openai_model,
            "messages": [
                {"role": "system", "content": "You are a VTEX catalog specialist."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return self._extract_json(content)

    def _ask_anthropic(self, attributes: Dict[str, List[str]]) -> Dict[str, Any]:
        prompt = (
            "From these attributes, identify the SINGLE most important attribute that should "
            "act as the SKU selector (Radio button) on the Home/Product page. "
            "All other attributes should be informational Product Specifications (Text).\n\n"
            f"Attributes: {json.dumps(attributes, ensure_ascii=False)}\n\n"
            'Return ONLY JSON with: {"selector_attribute":"...", "reason":"..."}'
        )
        headers = {
            "x-api-key": self.anthropic_api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.anthropic_model,
            "max_tokens": 300,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        text = ""
        if blocks and isinstance(blocks, list):
            text = "".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict))
        return self._extract_json(text)

    def choose_selector_attribute(self, attributes: Dict[str, List[str]]) -> Tuple[Optional[str], str]:
        """Select one attribute using LLM; fallback to first attribute."""
        if not attributes:
            return None, "No attributes found in product specifications."
        try:
            if self.openai_api_key:
                data = self._ask_openai(attributes)
            elif self.anthropic_api_key:
                data = self._ask_anthropic(attributes)
            else:
                raise ValueError("No OpenAI or Anthropic API key configured.")
            selector = str(data.get("selector_attribute", "")).strip()
            reason = str(data.get("reason", "")).strip()
            if selector and selector in attributes:
                return selector, reason or "LLM selected this attribute."
        except Exception as exc:
            self.logger.warning(f"LLM selector assessment failed, using fallback: {exc}")
        # Fallback heuristic: choose the attribute with highest variation across values.
        # This tends to select Size over static attributes like Color/Material when appropriate.
        sorted_attrs = sorted(
            attributes.items(),
            key=lambda kv: (-len(kv[1]), kv[0].lower())
        )
        fallback = sorted_attrs[0][0]
        return fallback, "Fallback by highest value variation because LLM result was unavailable."
