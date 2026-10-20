# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Reload doctypes to apply schema updates."""
	frappe.reload_doctype("Woo Settings")
	frappe.reload_doctype("Woo Item")
