# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, today


from woo_prime.api.auth import verify_api_secret


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_dashboard_stats():
	"""Endpoint for WordPress Admin Dashboard and Widget to display sync statistics & transaction logs."""
	verify_api_secret()
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

		# Fetch top 30 recent sync transaction logs (excluding request_data and response_data for security/PII protection)
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
				"error_message",
				"creation",
			],
			order_by="creation desc",
			limit=30,
		)

		# Format timestamps and truncate error messages
		for log in logs:
			if log.get("creation"):
				log["formatted_time"] = str(log["creation"])[:19]
			if log.get("error_message"):
				log["error_message"] = str(log["error_message"])[:300]

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
	except frappe.AuthenticationError:
		raise
	except Exception as e:
		frappe.log_error("Dashboard API Error", frappe.get_traceback())
		return {"status": "error", "message": str(e)}

