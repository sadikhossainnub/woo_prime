# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Reload doctypes to apply schema updates and create Woo Manager role if missing."""
	if not frappe.db.exists("Role", "Woo Manager"):
		role = frappe.new_doc("Role")
		role.role_name = "Woo Manager"
		role.desk_access = 1
		role.insert(ignore_permissions=True)

	frappe.reload_doctype("Woo Settings")
	frappe.reload_doctype("Woo Item")
