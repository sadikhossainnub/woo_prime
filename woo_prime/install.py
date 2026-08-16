# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe


def after_install():
	"""Run after app installation to set up custom fields and property setters."""
	create_custom_fields()
	create_property_setters()
	frappe.db.commit()


def _field_exists_on_doctype(doctype, fieldname):
	"""Return True if fieldname exists as a standard or custom field on the doctype."""
	meta = frappe.get_meta(doctype)
	return any(f.fieldname == fieldname for f in meta.fields)


def create_custom_fields():
	"""Create custom fields on Sales Order for WooCommerce tracking.

	Fields that already exist on the doctype (e.g. added by another installed app)
	are silently skipped to avoid a ValidationError during installation.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	all_fields = [
		{
			"fieldname": "woo_order_id",
			"fieldtype": "Data",
			"label": "WooCommerce Order ID",
			"insert_after": "po_date",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "sales_type",
			"fieldtype": "Data",
			"label": "Sales Type",
			"insert_after": "woo_order_id",
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "woo_order_status",
			"fieldtype": "Data",
			"label": "WooCommerce Order Status",
			"insert_after": "sales_type",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
		},
		{
			"fieldname": "woo_site",
			"fieldtype": "Data",
			"label": "WooCommerce Site",
			"insert_after": "woo_order_status",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
		},
	]

	# Skip any field that already exists on the doctype (standard or custom)
	fields_to_create = [
		f for f in all_fields
		if not _field_exists_on_doctype("Sales Order", f["fieldname"])
	]

	if not fields_to_create:
		return

	custom_fields = {"Sales Order": fields_to_create}
	create_custom_fields(custom_fields, update=True)


def create_property_setters():
	"""Add 'Site' to Sales Order order_type options."""
	existing = frappe.db.get_value(
		"Property Setter",
		{
			"doc_type": "Sales Order",
			"field_name": "order_type",
			"property": "options",
		},
		"value",
	)

	new_options = "\nSales\nMaintenance\nShopping Cart\nSite"

	if existing and "Site" in existing:
		# Already has Site option
		return

	# Create or update property setter
	if frappe.db.exists("Property Setter", {
		"doc_type": "Sales Order",
		"field_name": "order_type",
		"property": "options",
	}):
		frappe.db.set_value(
			"Property Setter",
			{
				"doc_type": "Sales Order",
				"field_name": "order_type",
				"property": "options",
			},
			"value",
			new_options,
		)
	else:
		ps = frappe.new_doc("Property Setter")
		ps.doctype_or_field = "DocField"
		ps.doc_type = "Sales Order"
		ps.field_name = "order_type"
		ps.property = "options"
		ps.property_type = "Text"
		ps.value = new_options
		ps.flags.ignore_permissions = True
		ps.insert()
