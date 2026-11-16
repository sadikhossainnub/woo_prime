# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Migration patch: Convert legacy 'Variant Item' woo_product_type variants to Woo Variant Group."""
	frappe.reload_doc("woo_prime", "doctype", "woo_variant_group")
	frappe.reload_doc("woo_prime", "doctype", "woo_item")
	frappe.reload_doc("woo_prime", "doctype", "woo_settings")

	# Find all Woo Items with woo_product_type == "Variant Item" that also have variant_of set
	legacy_variants = frappe.get_all(
		"Woo Item",
		filters={"woo_product_type": "Variant Item"},
		fields=["name", "item_code", "sku", "woo_product_id", "published", "item_name", "variant_of"],
	)

	for v in legacy_variants:
		if not v.variant_of:
			continue
		group_sku = f"{v.item_code}-GRP"
		# Ensure unique group_sku if collision
		if frappe.db.exists("Woo Item", {"sku": group_sku}):
			group_sku = f"{v.item_code}-VGRP"

		try:
			if not frappe.db.exists("Woo Variant Group", group_sku):
				grp = frappe.get_doc({
					"doctype": "Woo Variant Group",
					"group_sku": group_sku,
					"template_item": v.variant_of,
					"leader": v.name,
					"item_name": v.item_name,
					"woo_product_id": v.woo_product_id or 0,
					"published": v.published or 0,
				})
				grp.insert(ignore_permissions=True)

			frappe.db.set_value("Woo Item", v.name, {
				"woo_product_id": 0,
				"woo_product_type": "",
				"variant_publish_mode": "Separate Product",
				"woo_variant_group": group_sku,
				"published_as": "group-variation",
			})
			frappe.log_error(
				title="Woo Prime Migration: Variant Item to Group",
				message=f"Converted Woo Item '{v.name}' (item_code={v.item_code}) to Woo Variant Group '{group_sku}'",
			)
		except Exception as e:
			frappe.log_error(
				title=f"Woo Prime Migration Failed: {v.name}",
				message=frappe.get_traceback(),
			)

	frappe.db.commit()
