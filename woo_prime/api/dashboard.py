# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, today


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_dashboard_stats():
	"""Endpoint for WordPress Admin Dashboard and Widget to display sync statistics & transaction logs."""
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

		# Sync log counts
		success_count = frappe.db.count("Woo Sync Log", filters={"status": "Success"})
		failed_count = frappe.db.count("Woo Sync Log", filters={"status": "Failed"})

		# Last synced order
		last_order = frappe.db.get_value(
			"Sales Order",
			filters={"woo_order_id": ["is", "set"]},
			fieldname=["name", "woo_order_id", "grand_total", "creation"],
			order_by="creation desc",
			as_dict=True,
		)

		# Fetch top 30 recent sync transaction logs
		logs = frappe.db.get_all(
			"Woo Sync Log",
			fields=[
				"name",
				"sync_type",
				"direction",
				"status",
				"reference_doctype",
				"reference_name",
				"woo_reference_id",
				"request_data",
				"response_data",
				"error_message",
				"creation",
			],
			order_by="creation desc",
			limit=30,
		)

		# Format timestamps
		for log in logs:
			if log.get("creation"):
				log["formatted_time"] = str(log["creation"])[:19]

		return {
			"status": "success",
			"erpnext_url": frappe.utils.get_url(),
			"today_orders_count": today_orders,
			"week_orders_count": week_orders,
			"total_success_count": success_count,
			"total_failed_count": failed_count,
			"last_synced_order": last_order,
			"recent_transactions": logs,
		}
	except Exception as e:
		frappe.log_error("Dashboard API Error", frappe.get_traceback())
		return {"status": "error", "message": str(e)}
