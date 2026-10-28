# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import flt, today


from woo_prime.api.auth import verify_api_secret


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def calculate_cart_price(cart_data=None, customer_email=None, price_list=None):
	"""Calculate ERPNext Pricing Rules for WooCommerce Cart Items.

	Called by WooCommerce custom plugin/snippet at cart/checkout.

	Args:
		cart_data: JSON string or list of dicts:
			[{"sku": "SKU001", "item_code": "ITEM001", "qty": 2, "rate": 100}, ...]
		customer_email: Customer email to check Customer-specific pricing rules
		price_list: ERPNext Price List name (optional)

	Returns:
		dict: Evaluated cart items with applied pricing rules, discount amounts, and free items
	"""
	verify_api_secret()
	try:
		if isinstance(cart_data, str):
			cart_data = json.loads(cart_data)

		if not cart_data:
			return {"status": "error", "message": "cart_data is required"}

		settings = frappe.get_single("Woo Settings")
		price_list = price_list or settings.default_price_list

		# Find customer details if email provided
		customer_name = None
		customer_group = None
		territory = None
		if customer_email:
			customer_data = frappe.db.get_value(
				"Customer",
				{"email_id": customer_email},
				["name", "customer_group", "territory"],
				as_dict=True,
			)
			if isinstance(customer_data, dict):
				customer_name = customer_data.get("name")
				customer_group = customer_data.get("customer_group")
				territory = customer_data.get("territory")
			elif customer_data:
				customer_name = str(customer_data)

		if not customer_group:
			customer_group = frappe.db.get_single_value("Selling Settings", "customer_group")
			if not customer_group or frappe.db.get_value("Customer Group", customer_group, "is_group"):
				# Get first non-group Customer Group
				customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
				if not customer_group:
					customer_group = "All Customer Groups"  # Fallback for price calculation
		
		if not territory:
			territory = frappe.db.get_single_value("Selling Settings", "territory")
			if not territory or frappe.db.get_value("Territory", territory, "is_group"):
				# Get first non-group Territory
				territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
				if not territory:
					territory = "All Territories"  # Fallback for price calculation

		company = settings.default_company
		currency = None
		if price_list:
			currency = frappe.db.get_value("Price List", price_list, "currency")
		if not currency and company:
			currency = frappe.db.get_value("Company", company, "default_currency")
		if not currency:
			currency = getattr(settings, "currency", None) or "BDT"

		evaluated_items = []
		total_discount = 0.0

		for cart_item in cart_data:
			sku = cart_item.get("sku")
			item_code = cart_item.get("item_code")
			qty = flt(cart_item.get("qty", 1))
			rate = flt(cart_item.get("rate", 0))

			# Map SKU to item_code if item_code is not passed directly
			if not item_code and sku:
				woo_item = (
					frappe.db.get_value("Woo Item", {"sku": sku}, "item_code")
					or frappe.db.get_value("Woo Item", sku, "item_code")
				)
				item_code = woo_item or sku

			if not item_code or not frappe.db.exists("Item", item_code):
				evaluated_items.append({
					"sku": sku,
					"item_code": item_code,
					"qty": qty,
					"original_rate": rate,
					"final_rate": rate,
					"discount_amount": 0,
					"pricing_rule": None,
				})
				continue

			# Fetch price list rate if rate not passed
			if not rate:
				from woo_prime.api.sync import _get_item_price
				rate = _get_item_price(item_code, price_list)

			item_details = frappe.db.get_value("Item", item_code, ["item_group", "brand"], as_dict=True)
			if not isinstance(item_details, dict):
				item_details = {}
			item_group = item_details.get("item_group")
			brand = item_details.get("brand")

			# Call ERPNext pricing rule engine
			rule_args = frappe._dict({
				"doctype": "Sales Order",
				"parenttype": "Sales Order",
				"parent": None,
				"name": None,
				"item_code": item_code,
				"item_group": item_group,
				"brand": brand,
				"qty": qty,
				"rate": rate,
				"price_list": price_list,
				"currency": currency,
				"conversion_rate": 1.0,
				"plc_conversion_rate": 1.0,
				"transaction_type": "selling",
				"ignore_pricing_rule": 0,
				"customer": customer_name,
				"customer_group": customer_group,
				"territory": territory,
				"company": company,
				"transaction_date": today(),
			})

			try:
				from erpnext.accounts.doctype.pricing_rule.pricing_rule import (
					get_pricing_rule_for_item,
				)

				pricing_rule_result = get_pricing_rule_for_item(rule_args)
			except Exception:
				frappe.log_error(f"woo_prime: get_pricing_rule_for_item error for {item_code}", frappe.get_traceback())
				pricing_rule_result = {}

			pricing_rule_name = pricing_rule_result.get("pricing_rule")
			discount_percentage = flt(pricing_rule_result.get("discount_percentage", 0))
			discount_amount = flt(pricing_rule_result.get("discount_amount", 0))
			rule_rate = pricing_rule_result.get("rate")

			# Handle "Rate" type rules (rate in result overrides rate)
			if rule_rate is not None and (pricing_rule_result.get("pricing_rule_for") == "Rate" or (not discount_percentage and not discount_amount)):
				final_rate = flt(rule_rate)
				discount_amount = max(0.0, rate - final_rate)
			elif discount_percentage > 0:
				discount_amount = (rate * discount_percentage) / 100.0
				final_rate = max(0.0, rate - discount_amount)
			elif discount_amount > 0:
				final_rate = max(0.0, rate - discount_amount)
			elif rule_rate is not None and pricing_rule_name:
				final_rate = flt(rule_rate)
				discount_amount = max(0.0, rate - final_rate)
			else:
				final_rate = rate
				discount_amount = 0.0

			final_rate = max(0.0, final_rate)
			item_total_discount = discount_amount * qty
			total_discount += item_total_discount

			evaluated_item = {
				"sku": sku,
				"item_code": item_code,
				"qty": qty,
				"original_rate": rate,
				"final_rate": final_rate,
				"discount_per_unit": discount_amount,
				"total_discount": item_total_discount,
				"pricing_rule": pricing_rule_name,
			}
			if pricing_rule_result.get("free_item_data"):
				evaluated_item["free_item_data"] = pricing_rule_result.get("free_item_data")

			evaluated_items.append(evaluated_item)

		return {
			"status": "success",
			"items": evaluated_items,
			"total_cart_discount": total_discount,
		}

	except frappe.AuthenticationError:
		raise
	except Exception as e:
		frappe.log_error("Calculate Cart Price API Error", frappe.get_traceback())
		return {"status": "error", "message": str(e)}

