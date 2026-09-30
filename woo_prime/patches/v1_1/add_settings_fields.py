# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Ensure Woo Settings doctype fields are reloaded on migration."""
	frappe.reload_doctype("Woo Settings")
