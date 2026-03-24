"""Migration agent for import-to-vtex workflow."""
import os
from typing import Dict, Any

from .vtex_category_tree_agent import VTEXCategoryTreeAgent
from .vtex_product_sku_agent import VTEXProductSKUAgent
from .vtex_image_agent import VTEXImageAgent
from ..clients.vtex_client import VTEXClient
from ..utils.state_manager import save_state, STATE_DIR
from ..utils.logger import get_agent_logger
from ..tools.gemini_mapper import analyze_structure_from_sample


class MigrationAgent:
    """VTEX import coordinator from extracted legacy data."""
    
    def __init__(self):
        self.logger = get_agent_logger("migration_agent")
        
        self.vtex_client = None  # Initialize when needed
        self.vtex_category_tree_agent = None
        self.vtex_product_sku_agent = None
        self.vtex_image_agent = None
        
    def reporting_phase(self, legacy_site_data: Dict[str, Any]):
        """Generate migration report for selected products."""
        print("\n" + "="*60)
        print("📄 STEP 1: REPORTING")
        print("="*60)
        
        if not legacy_site_data or not legacy_site_data.get("products"):
            print("⚠️  No extracted products found.")
            return
        
        # Analyze structure
        print("\n📊 Analyzing catalog structure...")
        all_products_data = legacy_site_data.get("products", [])
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        structure = analyze_structure_from_sample(all_products_data, gemini_api_key)
        
        # Generate report
        report_lines = [
            "# VTEX Catalog Migration Plan",
            "",
            f"**Target Website:** {legacy_site_data.get('target_url', 'N/A')}",
            f"**Total Product URLs Found:** {legacy_site_data.get('metadata', {}).get('total_urls_found', 0)}",
            f"**Products Extracted:** {len(legacy_site_data.get('products', []))}",
            "",
            "## Catalog Structure",
            "",
            "### Departments",
        ]
        
        departments = structure.get("departments", [])
        for dept in departments:
            report_lines.append(f"- {dept}")
        
        report_lines.extend(["", "### Categories"])
        categories = structure.get("categories", [])
        for cat in categories:
            cat_name = cat.get("Name", "") if isinstance(cat, dict) else str(cat)
            dept = cat.get("Department", "") if isinstance(cat, dict) else ""
            report_lines.append(f"- {cat_name}" + (f" (Department: {dept})" if dept else ""))
        
        report_lines.extend(["", "### Brands"])
        brands = structure.get("brands", [])
        for brand in brands:
            report_lines.append(f"- {brand}")

        report_lines.extend([
            "",
            "## Product Counts",
            "",
            f"- **Total Products:** {structure.get('total_products', 0)}",
            f"- **Has Variations:** {structure.get('product_patterns', {}).get('has_variations', False)}",
            "",
            "## Next Steps",
            "",
            "1. Review the catalog structure above",
            "2. Type 'APPROVED' to begin execution",
            ""
        ])
        
        report_content = "\n".join(report_lines)
        
        # Save report in state folder
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        report_path = STATE_DIR / "final_plan.md"
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        
        print(f"\n✅ Report generated: {report_path}")
        print("\n" + "="*60)
        print("REPORT PREVIEW")
        print("="*60)
        print(report_content)
        
        save_state("reporting", {
            "structure": structure,
            "report_path": str(report_path)
        })

    def _set_sku_specifications(self, sku_id: int, sku_data: Dict[str, Any]) -> int:
        """Set SKU specification values using the catalog endpoint."""
        specifications = sku_data.get("Specifications", []) or []
        if not specifications:
            return 0

        success_count = 0
        for spec in specifications:
            field_name = str(spec.get("Name", "")).strip()
            field_value = spec.get("Value")
            if not field_name or field_value in [None, ""]:
                continue

            try:
                ok = self.vtex_client.set_sku_specification_values(
                    sku_id=sku_id,
                    field_name=field_name,
                    field_values=[str(field_value)],
                    group_name="Specifications",
                    root_level_specification=False,
                )
                if ok:
                    success_count += 1
                    print(f"       📋 Specification set: {field_name} = {field_value}")
                else:
                    print(f"       ⚠️  Failed to set specification: {field_name}")
            except Exception as spec_error:
                self.logger.warning(
                    f"Could not set specification '{field_name}' for SKU {sku_id}: {spec_error}"
                )
                print(f"       ⚠️  Failed to set specification {field_name}: {spec_error}")

        return success_count
    
    def execution_phase(
        self,
        legacy_site_data: Dict[str, Any],
        require_approval: bool = True,
        use_json_image_urls: bool = False
    ):
        """Create catalog entities in VTEX."""
        if require_approval:
            while True:
                print("\n" + "="*60)
                print("⚠️  READY TO EXECUTE")
                print("="*60)
                print("\nOptions:")
                print("  - Type 'APPROVED' to proceed with execution")
                print("  - Type 'RETRY' to regenerate the report and review again")
                print("  - Type 'CANCEL' to end execution")
                
                approval = input("\nWhat would you like to do? ").strip().upper()
                
                if approval == "APPROVED":
                    break
                elif approval == "CANCEL":
                    print("\n❌ Execution cancelled by user.")
                    return
                elif approval == "RETRY":
                    print("\n🔄 Regenerating report...")
                    self.reporting_phase(legacy_site_data)
                    # Loop back to ask for approval again
                    continue
                else:
                    print(f"\n⚠️  Invalid option: '{approval}'. Please type 'APPROVED', 'RETRY', or 'CANCEL'.")
                    continue
        
        print("\n" + "="*60)
        print("🚀 STEP 2: EXECUTION - VTEX Catalog Import")
        print("="*60)
        
        # Initialize VTEX client
        try:
            self.vtex_client = VTEXClient()
            print("✅ VTEX client initialized")
            self.logger.info("VTEX client initialized")
        except Exception as e:
            print(f"❌ VTEX credentials not configured: {e}")
            print("   Set VTEX_ACCOUNT_NAME, VTEX_APP_KEY, and VTEX_APP_TOKEN in .env")
            self.logger.error(f"VTEX credentials not configured: {e}")
            return
        
        # Initialize VTEX agents (product agent can call category tree agent to create missing categories)
        self.vtex_category_tree_agent = VTEXCategoryTreeAgent(self.vtex_client)
        # Specifications are disabled - no specification fields will be created or set
        self.vtex_product_sku_agent = VTEXProductSKUAgent(
            self.vtex_client,
            category_tree_agent=self.vtex_category_tree_agent
        )
        self.vtex_image_agent = VTEXImageAgent(self.vtex_client)
        
        # Step 1: Create category tree
        print("\n📂 Creating category tree...")
        vtex_category_tree = self.vtex_category_tree_agent.create_category_tree(legacy_site_data)

        # Step 2: Create products, SKUs, set SKU specification values, and associate images
        print("\n📦 Creating products, SKUs, setting SKU specs, and associating images...")
        print("   Order: Product → SKU → SKU Spec Values → Images")
        if use_json_image_urls:
            print("   Image strategy: use JSON URLs directly")
        else:
            print("   Image strategy: upload to GitHub then associate")
        
        products = legacy_site_data.get("products", [])
        print(f"\n📦 Processing {len(products)} products...")
        
        all_image_results = {}
        
        for i, product_data in enumerate(products, 1):
            print(f"\n   [{i}/{len(products)}] Processing product...")
            self.logger.info(f"Processing product {i}/{len(products)}")
            
            try:
                # Create product (specifications disabled - pass empty dict)
                product_info = self.vtex_product_sku_agent.create_single_product(
                    product_data,
                    vtex_category_tree,
                    {"specification_fields": {}, "summary": {"fields_created": 0}}
                )
                
                # If category tree was updated (e.g. missing categories created), use it for next products
                if product_info and product_info.get("vtex_category_tree") is not None:
                    vtex_category_tree = product_info["vtex_category_tree"]
                
                if not product_info:
                    self.logger.warning(f"Failed to create product {i}, skipping")
                    continue
                
                product_id = product_info["id"]
                product_url = product_data.get("url", f"product_{product_id}")
                
                # Get SKUs for this product
                skus = product_data.get("skus", [])
                if not skus:
                    # Create default SKU
                    skus = [{
                        "Name": "Default",
                        "EAN": f"EAN{product_id}",
                        "IsActive": True
                    }]
                
                # Create each SKU, set SKU specification values, and then associate images
                for sku_data in skus:
                    # Create SKU
                    sku_info = self.vtex_product_sku_agent.create_single_sku(
                        product_id=product_id,
                        product_url=product_url,
                        sku_data=sku_data
                    )
                    
                    if not sku_info:
                        self.logger.warning(f"Failed to create SKU for product {product_id}, skipping")
                        continue
                    
                    sku_id = sku_info["id"]
                    sku_name = sku_info["name"]
                    
                    # Step 1: Set SKU specification values before image upload.
                    specs_set = self._set_sku_specifications(sku_id, sku_data)
                    if specs_set == 0 and sku_data.get("Specifications"):
                        self.logger.info(f"No SKU specifications were set for SKU {sku_id}")

                    # Step 2: Associate images with this SKU (VTEX requires files before SKU can be active)
                    # New import format keeps images at SKU level.
                    images = sku_data.get("images", []) or product_data.get("images", [])
                    had_images = False
                    if images:
                        image_result = self.vtex_image_agent.associate_images_with_sku(
                            sku_id=sku_id,
                            sku_name=sku_name,
                            image_urls=images,
                            use_json_image_urls=use_json_image_urls
                        )
                        all_image_results[str(sku_id)] = image_result
                        had_images = (image_result.get("total_associated") or 0) > 0
                    else:
                        self.logger.info(f"No images found for SKU {sku_id}")
                    
                    # Step 3: Activate SKU only after images are associated (VTEX requires files before IsActive=true)
                    if had_images:
                        try:
                            self.vtex_client.update_sku(sku_id, is_active=True)
                            print(f"       ✓ SKU activated (IsActive=true)")
                        except Exception as activate_error:
                            self.logger.warning(f"Could not activate SKU {sku_id}: {activate_error}")
                            print(f"       ⚠️  Failed to activate SKU: {activate_error}")
                    else:
                        print(f"       ℹ️  SKU left inactive (no images; VTEX requires files before activating)")
                    
                    # Step 4: Set price for this SKU
                    # Order: Create SKU > Add specs > Add images > Add price > Add inventory
                    # Price from website is set as basePrice with markup=0
                    try:
                        price_value = sku_data.get("Price") or 0
                        list_price_value = sku_data.get("ListPrice") or price_value
                        self.vtex_client.set_sku_price(sku_id, price_value, list_price_value)
                        print(f"       💰 Price set: {price_value} (basePrice, markup=0)")
                    except Exception as price_error:
                        self.logger.warning(f"Could not set price for SKU {sku_id}: {price_error}")
                        print(f"       ⚠️  Failed to set price: {price_error}")
                    
                    # Step 5: Set inventory for this SKU in all warehouses
                    # Inventory is set to 100 for all available warehouses
                    try:
                        inventory_results = self.vtex_client.set_sku_inventory_all_warehouses(
                            sku_id=sku_id,
                            quantity=100  # Set to 100 for all warehouses
                        )
                        successful_warehouses = sum(1 for r in inventory_results.values() if r.get("success", False))
                        print(f"       📦 Inventory set to 100 in {successful_warehouses}/{len(inventory_results)} warehouse(s)")
                    except Exception as inventory_error:
                        self.logger.warning(f"Could not set inventory for SKU {sku_id}: {inventory_error}")
                        print(f"       ⚠️  Failed to set inventory: {inventory_error}")
                
            except Exception as e:
                self.logger.error(f"Error processing product {i}: {e}", exc_info=True)
                print(f"     ⚠️  Error processing product: {e}")
                continue
        
        # Format outputs
        vtex_products = self.vtex_product_sku_agent._format_output()
        
        # Save product/SKU state
        save_state("vtex_products_skus", vtex_products)
        
        # Format image results
        vtex_images = self.vtex_image_agent._format_output()
        
        # Save image state
        save_state("vtex_images", vtex_images)
        
        # Save execution summary
        save_state("execution", {
            "departments_created": vtex_category_tree.get("summary", {}).get("departments_created", 0),
            "categories_created": vtex_category_tree.get("summary", {}).get("categories_created", 0),
            "brands_created": vtex_category_tree.get("summary", {}).get("brands_created", 0),
            "products_created": vtex_products.get("summary", {}).get("products_created", 0),
            "skus_created": vtex_products.get("summary", {}).get("skus_created", 0),
            "images_uploaded": vtex_images.get("summary", {}).get("total_images_associated", vtex_images.get("summary", {}).get("total_images_uploaded", 0))
        })
        
        print("\n" + "="*60)
        print("✅ EXECUTION COMPLETE")
        print("="*60)
        print(f"   Departments: {vtex_category_tree.get('summary', {}).get('total_departments', 0)}")
        print(f"   Categories: {vtex_category_tree.get('summary', {}).get('total_categories', 0)}")
        print(f"   Brands: {vtex_category_tree.get('summary', {}).get('total_brands', 0)}")
        print(f"   Products: {vtex_products.get('summary', {}).get('total_products', 0)}")
        print(f"   SKUs: {vtex_products.get('summary', {}).get('total_skus', 0)}")
        print(f"   Images: {vtex_images.get('summary', {}).get('total_images_uploaded', 0)}")
        print("   Note: SKU specification values are set during SKU processing")
        
        self.logger.info("Execution phase complete")

