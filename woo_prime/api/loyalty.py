# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, today


from woo_prime.api.auth import verify_api_secret


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_customer_loyalty_points(customer_email=None, phone=None, customer_name=None):
	"""Get Customer Loyalty Points balance and redemption value.

	Called by WooCommerce custom plugin/snippet to show balance & allow redemption.

	Args:
		customer_email: Customer email
		phone: Customer phone
		customer_name: ERPNext Customer name

	Returns:
		dict: loyalty_points, conversion_factor, redeemable_amount
	"""
	verify_api_secret()
	try:
		# Find customer
		if not customer_name:
			if customer_email:
				customer_name = frappe.db.get_value("Customer", {"email_id": customer_email}, "name")
			if not customer_name and phone:
				customer_name = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")

		if not customer_name:
			return {
				"status": "success",
				"customer": None,
				"loyalty_points": 0,
				"conversion_factor": 1.0,
				"redeemable_amount": 0.0,
			}

		# Get Loyalty Point balance and conversion factor via get_loyalty_program_details_with_points
		loyalty_points = 0
		conversion_factor = 1.0
		try:
			settings = frappe.get_single("Woo Settings")
			company = getattr(settings, "default_company", None)
			from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
				get_loyalty_program_details_with_points,
			)

			lp_details = get_loyalty_program_details_with_points(
				customer_name,
				company=company,
				silent=True,
			)
			if lp_details:
				loyalty_points = flt(lp_details.get("loyalty_points", 0))
				conversion_factor = flt(lp_details.get("conversion_factor", 1.0)) or 1.0
		except Exception:
			# Fallback: try legacy get_loyalty_point_balance + separate program lookup
			try:
				from erpnext.accounts.doctype.loyalty_point_entry.loyalty_point_entry import (
					get_loyalty_point_balance,
				)

				loyalty_points = flt(get_loyalty_point_balance(customer_name))
				loyalty_program = frappe.db.get_value("Customer", customer_name, "loyalty_program")
				if loyalty_program:
					conversion_factor = flt(
						frappe.db.get_value("Loyalty Program", loyalty_program, "conversion_factor")
					) or 1.0
			except Exception:
				frappe.log_error(
					f"woo_prime: loyalty points lookup error for {customer_name}",
					frappe.get_traceback(),
				)

		redeemable_amount = flt(loyalty_points) * flt(conversion_factor)

		return {
			"status": "success",
			"customer": customer_name,
			"loyalty_points": loyalty_points,
			"conversion_factor": conversion_factor,
			"redeemable_amount": redeemable_amount,
		}

	except frappe.AuthenticationError:
		raise
	except Exception as e:
		frappe.log_error("Get Loyalty Points API Error", frappe.get_traceback())
		return {"status": "error", "message": str(e)}

