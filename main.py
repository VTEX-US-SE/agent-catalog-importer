#!/usr/bin/env python3
"""Main entry point for VTEX import from existing extraction data."""
import sys
import os
import argparse

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from vtex_agent.agents.migration_agent import MigrationAgent
from vtex_agent.utils.state_manager import load_state


def run_import_to_vtex_only(use_json_image_urls: bool = False):
    """
    Import existing catalog_content.json directly to VTEX.
    
    This skips the extraction phase and imports the data from
    catalog_content.json directly into VTEX.
    """
    print("\n" + "="*60)
    print("🚀 VTEX DIRECT IMPORT")
    print("="*60)
    print("Loading existing extraction data...")
    
    # Load existing extraction data
    legacy_site_data = load_state("catalog_content")
    
    if not legacy_site_data:
        print("\n❌ Error: catalog_content.json not found!")
        print("   Expected location: state/catalog_content.json")
        print("\n   Please ensure the file exists before running import.")
        sys.exit(1)
    
    products = legacy_site_data.get("products", [])
    if not products:
        print("\n❌ Error: No products found in catalog_content.json!")
        print("   The file exists but contains no product data.")
        sys.exit(1)
    
    total_products = len(products)
    print(f"✅ Loaded {total_products} products from catalog_content.json")
    print(f"   Target URL: {legacy_site_data.get('target_url', 'N/A')}")

    # Ask how many products to import; this will define
    # which categories, brands, products and SKUs are created.
    while True:
        selection = input(
            f"\nHow many products would you like to import to VTEX? "
            f"(1-{total_products}, or 'all' for all): "
        ).strip().lower()
        
        if selection == "all":
            selected_products = products
            break
        
        try:
            count = int(selection)
            if 1 <= count <= total_products:
                # Use the first N products from the extracted list
                selected_products = products[:count]
                break
            else:
                print(f"⚠️  Please enter a number between 1 and {total_products}, or 'all'.")
        except ValueError:
            print("⚠️  Invalid input. Please enter a number or 'all'.")

    print(f"\n✅ Will import {len(selected_products)} product(s) to VTEX.")
    print("   Categories, brands, products and SKUs will be created only")
    print("   for these selected products.")
    if use_json_image_urls:
        print("   Image mode: using image URLs directly from JSON (no GitHub upload).")
    else:
        print("   Image mode: uploading images to GitHub before VTEX association.")

    # Limit legacy_site_data to the selected products
    limited_legacy_site_data = {
        **legacy_site_data,
        "products": selected_products,
    }
    
    # Initialize migration agent
    agent = MigrationAgent()
    
    print("\n" + "="*60)
    print("📄 Running reporting phase...")
    print("="*60)
    agent.reporting_phase(limited_legacy_site_data)
    print("\n✅ Reporting complete. Review state/final_plan.md if needed.")
    
    # Run execution phase
    print("\n" + "="*60)
    print("🚀 Starting VTEX import...")
    print("="*60)
    
    agent.execution_phase(
        limited_legacy_site_data,
        require_approval=True,
        use_json_image_urls=use_json_image_urls
    )
    
    print("\n" + "="*60)
    print("✅ IMPORT COMPLETE")
    print("="*60)


def main():
    """Main entry point for direct VTEX import."""
    parser = argparse.ArgumentParser(description="Import catalog data to VTEX")
    parser.add_argument(
        "--use-json-image-urls",
        action="store_true",
        help="Use image URLs from input JSON directly, without uploading to GitHub first"
    )
    args = parser.parse_args()

    try:
        run_import_to_vtex_only(use_json_image_urls=args.use_json_image_urls)
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Operation interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
