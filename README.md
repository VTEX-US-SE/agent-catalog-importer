# VTEX catalog importer (direct import)

This project imports a **pre-built** product catalog from `state/catalog_content.json` into VTEX. The CLI entry point is `main.py`: it loads that file, asks how many products to import, runs a **reporting** phase (Gemini analyzes structure and writes `state/final_plan.md`), then an **execution** phase that creates departments, categories, brands, products, SKUs, images, prices, and inventory via the VTEX APIs—with an `APPROVED` gate before writes.

It does **not** crawl legacy sites or run the old discovery → mapping → HTML extraction loop; you supply `catalog_content.json` yourself (or from another tool).

## Features

- VTEX catalog writes in dependency order (categories → brands → products → SKUs → images → price/inventory)
- **Reporting**: Gemini summarizes catalog structure into `state/final_plan.md`
- **Images**: download + upload to GitHub + associate in VTEX, **or** pass `--use-json-image-urls` to use URLs from JSON directly
- **SKU specifications**: selector assessment (optional OpenAI/Anthropic) and spec sync where implemented in `MigrationAgent`
- State snapshots under `state/` with numbered JSON filenames for the import pipeline
- Retries/backoff for LLM and API calls where implemented

## Requirements

- Python 3.10+ recommended (project tested with 3.13)
- A valid `state/catalog_content.json` with a `products` array
- Dependencies: see [requirements.txt](requirements.txt)

## Setup

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp env_template.txt .env
# Edit .env
```

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GEMINI_API_KEY` | Yes (for reporting) | Google Gemini API key |
| `GEMINI_MODEL` | No | Model name; default in code: `gemini-2.0-flash` |
| `GEMINI_BASE_URL` | No | Only if you need an explicit Generative Language API base URL |
| `VTEX_ACCOUNT_NAME` | Yes | VTEX account |
| `VTEX_APP_KEY` | Yes | VTEX app key |
| `VTEX_APP_TOKEN` | Yes | VTEX app token |
| `VTEX_WAREHOUSE_ID` | No | Warehouse for inventory (default `1_1`) |
| `GITHUB_TOKEN` | If using GitHub images | PAT with repo contents write |
| `GITHUB_REPO` | If using GitHub images | `owner/repo` or GitHub URL |
| `GITHUB_BRANCH` | No | Branch for uploads (default `main`) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | No | SKU selector assessment |
| `OPENAI_MODEL` / `ANTHROPIC_MODEL` | No | Overrides for assessor models |

Copy [env_template.txt](env_template.txt) to `.env` and adjust values. The app loads `.env` via `python-dotenv` where `load_dotenv()` is called in the VTEX/Gemini/image modules.

**LLM SDK:** `vtex_agent/tools/gemini_mapper.py` uses the `google-genai` package and can fall back to `google-generativeai` if the new SDK is missing (install the optional package from the comment in `requirements.txt` if you rely on that fallback).

## Usage

```bash
python main.py

# Use image URLs from JSON in VTEX (no GitHub upload)
python main.py --use-json-image-urls
```

Interactive prompts:

1. How many products to import (`1`–`N` or `all`)
2. After the report, type `APPROVED` to run VTEX API calls (when `require_approval=True`)

## State files (`state/`)

`state_manager.py` maps step names to numbered files for this import workflow:

| File | Role |
|------|------|
| `catalog_content.json` | **Input** — extracted catalog (no numeric prefix) |
| `01_reporting.json` | Structure analysis + report path |
| `final_plan.md` | Human-readable plan (next to JSON state) |
| `02_vtex_category_tree.json` | Departments, categories, brands |
| `03_vtex_products_skus.json` | Products and SKUs |
| `04_vtex_images.json` | Image associations |
| `05_execution.json` | Execution summary |
| `06_vtex_specifications.json` | Specification-related state (when used) |
| `07_field_type_overrides.json` | Field overrides (when used) |
| `08_vtex_selector_execution.json` | Selector execution (when used) |
| `custom_prompt.json` | Optional; not used by `main.py` direct import |

## Input shape (`catalog_content.json`)

The loader expects JSON with at least:

- `products`: list of product payloads
- Optional: `target_url`, `metadata` (used in the generated report)

Each product should align with what `MigrationAgent` and the VTEX agents expect: `product` (name, ids, descriptions), `categories`, `brand`, `skus` with pricing/ref ids, and **SKU-level `images`** when possible (product-level `images` may still work as fallback).

## Image modes

- **Default:** images are downloaded, uploaded to `GITHUB_REPO` on `GITHUB_BRANCH`, and VTEX is given `raw.githubusercontent.com` URLs.
- **`--use-json-image-urls`:** skips GitHub; URLs from JSON are passed through to VTEX (they must be reachable by VTEX).

## Project layout

```
vtex-poc-agent-catalog-importer/
├── main.py                 # CLI: load catalog_content → report → execute
├── vtex_agent/
│   ├── agents/             # Migration, category tree, products/SKUs, images, specs
│   ├── clients/vtex_client.py
│   ├── tools/              # gemini_mapper, image_manager, sku_selector_assessor
│   └── utils/              # state, logging, validation, retries
├── state/                  # local state (gitignored as appropriate)
├── requirements.txt
├── env_template.txt
└── .env                    # create locally (not committed)
```

## Troubleshooting

- **`catalog_content.json` not found** — path must be `state/catalog_content.json` relative to the project root.
- **`GEMINI_API_KEY` missing** — reporting calls Gemini; set the key in `.env`.
- **VTEX 401/403** — check `VTEX_ACCOUNT_NAME`, `VTEX_APP_KEY`, and `VTEX_APP_TOKEN` and catalog permissions.
- **GitHub upload errors** — confirm `GITHUB_TOKEN` (repo scope) and `GITHUB_REPO`; with `--use-json-image-urls` you can avoid GitHub entirely if URLs are public and stable.

## Example session (abbreviated)

```
============================================================
🚀 VTEX DIRECT IMPORT
============================================================
Loading existing extraction data...
✅ Loaded 42 products from catalog_content.json

How many products would you like to import to VTEX? (1-42, or 'all' for all): 5

============================================================
📄 Running reporting phase...
============================================================
✅ Report generated: .../state/final_plan.md

============================================================
🚀 Starting VTEX import...
============================================================
⚠️  Ready to execute? Type 'APPROVED' to proceed: APPROVED
...
✅ IMPORT COMPLETE
```

After import, verify catalog, images, and inventory in VTEX Admin.
