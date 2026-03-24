"""VTEX Specification Agent - AI assessment and specification sync."""
import time
from typing import Any, Dict, List, Optional

from ..clients.vtex_client import VTEXClient
from ..tools.sku_selector_assessor import SKUSelectorAssessor
from ..utils.logger import get_agent_logger
from ..utils.state_manager import save_state


class VTEXSpecificationAgent:
    """Runs two-phase specification flow: assessment then execution."""

    def __init__(self, vtex_client: Optional[VTEXClient] = None, vtex_category_tree_agent=None):
        self.logger = get_agent_logger("vtex_specification_agent")
        self.vtex_client = vtex_client
        self.vtex_category_tree_agent = vtex_category_tree_agent
        self.sku_selector_assessor = SKUSelectorAssessor()

    def _resolve_department_name(self, product: Dict[str, Any]) -> str:
        """Resolve root department name from JSON categories."""
        categories = product.get("categories", []) or []
        if categories:
            return str(categories[0].get("Name", "")).strip()
        return ""

    def build_selector_assessment(self, legacy_site_data: Dict[str, Any]) -> Dict[str, Any]:
        products = legacy_site_data.get("products", [])
        by_department: Dict[str, Dict[str, Any]] = {}
        for product in products:
            dept_name = self._resolve_department_name(product)
            if not dept_name:
                continue
            item = by_department.setdefault(
                dept_name,
                {"department_name": dept_name, "attributes": {}},
            )
            attrs = self.sku_selector_assessor._extract_product_attributes(product)
            for attr_name, values in attrs.items():
                bucket = item["attributes"].setdefault(attr_name, [])
                for value in values:
                    if value not in bucket:
                        bucket.append(value)

        plan_rows = []
        selector_by_department = {}

        for department_name, item in by_department.items():
            attributes = item.get("attributes", {})
            selector_attr, reason = self.sku_selector_assessor.choose_selector_attribute(attributes)
            selector_by_department[department_name] = {
                "department_name": department_name,
                "selector_attribute": selector_attr,
                "reason": reason,
            }

            for attr_name in attributes.keys():
                is_selector = (attr_name == selector_attr)
                field_type_id = 6 if is_selector else 1
                plan_rows.append({
                    "DepartmentName": department_name,
                    "Field_Name": attr_name,
                    "Action": "Create",
                    "Type": "Radio" if is_selector else "Text",
                    "FieldTypeId": field_type_id,
                    "IsSKUSelector": is_selector,
                    "IsStockKeepingUnit": is_selector,
                })

        # enforce one selector per category
        rows_by_department = {}
        for row in plan_rows:
            rows_by_department.setdefault(row["DepartmentName"], []).append(row)
        for cat_rows in rows_by_department.values():
            selectors = [r for r in cat_rows if r["IsSKUSelector"]]
            if len(selectors) > 1:
                keep = selectors[0]["Field_Name"]
                for row in cat_rows:
                    if row["Field_Name"] != keep:
                        row["IsSKUSelector"] = False
                        row["IsStockKeepingUnit"] = False
                        row["Type"] = "Text"
                        row["FieldTypeId"] = 1

        return {
            "departments": selector_by_department,
            "validation_tree": plan_rows,
            "summary": {
                "departments_assessed": len(selector_by_department),
                "fields_planned": len(plan_rows),
            },
        }

    def format_assessment_preview(self, assessment: Dict[str, Any]) -> str:
        rows = assessment.get("validation_tree", [])
        by_dept: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_dept.setdefault(row.get("DepartmentName", "Unknown"), []).append(row)
        lines = ["### Specifications"]
        for dept_name in sorted(by_dept.keys()):
            lines.append(f"- {dept_name}")
            for row in by_dept[dept_name]:
                label = row["Field_Name"]
                if row.get("IsSKUSelector"):
                    label += " (SKU selector)"
                lines.append(f"-- {label}")
        if len(lines) == 1:
            lines.append("- No specifications found")
        return "\n".join(lines)

    def execute_selector_plan(self, assessment: Dict[str, Any], legacy_site_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.vtex_client or not self.vtex_category_tree_agent:
            raise ValueError("VTEXSpecificationAgent requires vtex_client and vtex_category_tree_agent for execution.")

        validation_tree = assessment.get("validation_tree", [])
        group_sync_results = []
        group_sync_errors = []
        field_sync_results = []
        field_sync_errors = []

        # Map department names from phase 1 to VTEX department IDs from created tree.
        department_id_by_name = {}
        for _, dept in (self.vtex_category_tree_agent.departments or {}).items():
            name = str(dept.get("name", "")).strip()
            dept_id = dept.get("id")
            if name and dept_id is not None:
                department_id_by_name[name.lower()] = int(dept_id)

        # 1) Create one "Specifications" group for each Department.
        planned_departments = sorted({str(row.get("DepartmentName", "")).strip() for row in validation_tree if row.get("DepartmentName")})
        for dept_name in planned_departments:
            category_id = department_id_by_name.get(dept_name.lower())
            if category_id is None:
                msg = f"Department '{dept_name}' not found in VTEX category tree; skipping specification group."
                self.logger.warning(msg)
                group_sync_errors.append(msg)
                continue
            group_result = self.vtex_client.create_specification_group_for_category(
                category_id=category_id,
                group_name="Specifications",
            )
            if group_result:
                group_sync_results.append({
                    "department_name": dept_name,
                    "category_id": category_id,
                    "group_name": "Specifications",
                    "success": True,
                })
            else:
                msg = f"Specification group creation failed for department='{dept_name}' (category={category_id})."
                self.logger.warning(msg)
                group_sync_errors.append(msg)
            time.sleep(0.2)

        # 2) Create specification fields at department/category level (no values in this phase).
        for row in validation_tree:
            dept_name = str(row.get("DepartmentName", "")).strip()
            category_id = department_id_by_name.get(dept_name.lower())
            if category_id is None:
                msg = f"Department '{dept_name}' not found in VTEX category tree; skipping field '{row.get('Field_Name')}'."
                self.logger.warning(msg)
                field_sync_errors.append(msg)
                continue
            field_name = row["Field_Name"]
            existing_fields = self.vtex_client.get_fields_by_collection(category_id)
            existing = None
            for field in existing_fields:
                if isinstance(field, dict) and str(field.get("Name", "")).strip() == field_name:
                    existing = field
                    break
            result = self.vtex_client.upsert_category_specification_field(
                category_id=category_id,
                field_name=field_name,
                field_type_id=row["FieldTypeId"],
                is_sku_selector=row["IsSKUSelector"],
                existing_field=existing,
            )
            if result:
                field_sync_results.append({
                    "department_name": dept_name,
                    "category_id": category_id,
                    "field_name": field_name,
                    "action": row["Action"],
                    "type": row["Type"],
                    "is_sku_selector": row["IsSKUSelector"],
                    "success": True,
                })
            else:
                msg = (
                    f"Field sync failed for department='{dept_name}' (category={category_id}), field='{field_name}'. "
                    "Possible VTEX inheritance or validation restriction."
                )
                self.logger.warning(msg)
                field_sync_errors.append(msg)
            time.sleep(0.2)

        return {
            "group_sync_results": group_sync_results,
            "group_sync_errors": group_sync_errors,
            "field_sync_results": field_sync_results,
            "field_sync_errors": field_sync_errors,
            "sku_sync_results": [],
            "sku_sync_errors": [],
        }

    def run_two_phase(self, legacy_site_data: Dict[str, Any]) -> None:
        print("\n" + "="*60)
        print("🧠 PHASE 1: AI-DRIVEN SKU SELECTOR ASSESSMENT")
        print("="*60)
        assessment = self.build_selector_assessment(legacy_site_data)
        save_state("vtex_specifications", assessment)
        print("\n" + self.format_assessment_preview(assessment))
        print(f"✅ Assessment generated with {assessment.get('summary', {}).get('fields_planned', 0)} planned field mappings")
        print("   Saved for review in state/11_vtex_specifications.json")

        while True:
            phase_approval = input(
                "\nReview validation tree and type 'APPROVED' to execute phase 2, "
                "'RETRY' to reassess, or 'CANCEL' to stop: "
            ).strip().upper()
            if phase_approval == "APPROVED":
                break
            if phase_approval == "RETRY":
                assessment = self.build_selector_assessment(legacy_site_data)
                save_state("vtex_specifications", assessment)
                print("🔄 Assessment regenerated.")
                continue
            if phase_approval == "CANCEL":
                print("❌ Execution cancelled before phase 2.")
                return
            print("⚠️  Invalid option. Type APPROVED, RETRY, or CANCEL.")

        print("\n" + "="*60)
        print("⚙️  PHASE 2: EXECUTION (FIELDS + SKU SPEC VALUES)")
        print("="*60)
        selector_execution = self.execute_selector_plan(assessment, legacy_site_data)
        save_state("vtex_selector_execution", selector_execution)
        print(
            f"✅ Selector execution: groups_ok={len(selector_execution.get('group_sync_results', []))}, "
            f"groups_error={len(selector_execution.get('group_sync_errors', []))}, "
            f"fields_ok={len(selector_execution.get('field_sync_results', []))}, "
            f"fields_error={len(selector_execution.get('field_sync_errors', []))}, "
            f"skus_ok={len(selector_execution.get('sku_sync_results', []))}, "
            f"skus_error={len(selector_execution.get('sku_sync_errors', []))}"
        )
