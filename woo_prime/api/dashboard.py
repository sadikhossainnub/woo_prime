# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, today


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_dashboard_stats():
	"""Endpoint for WordPress Admin Dashboard widget to display sync statistics."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled:
			return {
				"status": "disabled",
				"message": "WooCommerce integration is disabled in ERPNext.",
			}

		# Sync stats for today
		today_orders = frappe.db.count(
			"Sales Order",
			filters={"creation": [">=", today()], "woo_order_id": ["is", "set"]},
		)

		# Sync stats for last 7 days
		week_orders = frappe.db.count(
			"Sales Order",
			filters={"creation": [">=", add_days(today(), -7)], "woo_order_id": ["is", "set"]},
		)

		# Last synced order
		last_order = frappe.db.get_value(
			"Sales Order",
			filters={"woo_order_id": ["is", "set"]},
			fieldname=["name", "woo_order_id", "grand_total", "creation"],
			order_by="creation desc",
			as_dict=True,
		)

		# Last sync log status
		last_log = frappe.db.get_value(
			"Woo Sync Log",
			fieldname=["status", "creation", "error_message"],
			order_by="creation desc",
			as_dict=True,
		)

		return {
			"status": "success",
			"erpnext_url": frappe.utils.get_url(),
			"today_orders_count": today_orders,
			"week_orders_count": week_orders,
			"last_synced_order": last_order,
			"last_log": last_log,
		}
	except Exception as e:
		frappe.log_error("Dashboard API Error", frappe.get_traceback())
		return {"status": "error", "message": str(e)}
