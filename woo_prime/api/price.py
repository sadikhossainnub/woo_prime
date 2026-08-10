# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import flt, today


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
	try:
		if isinstance(cart_data, str):
			cart_data = json.loads(cart_data)

		if not cart_data:
			return {"status": "error", "message": "cart_data is required"}

		settings = frappe.get_single("Woo Settings")
		price_list = price_list or settings.default_price_list

		# Find customer name if email provided
		customer_name = None
		if customer_email:
			customer_name = frappe.db.get_value("Customer", {"email_id": customer_email}, "name")

		company = settings.default_company

		evaluated_items = []
		total_discount = 0.0

		for cart_item in cart_data:
			sku = cart_item.get("sku")
			item_code = cart_item.get("item_code")
			qty = flt(cart_item.get("qty", 1))
			rate = flt(cart_item.get("rate", 0))

			# Map SKU to item_code if item_code is not passed directly
			if not item_code and sku:
				woo_item = frappe.db.get_value("Woo Item", sku, "item_code")
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
				price_list_rate = frappe.db.get_value(
					"Item Price",
					{"item_code": item_code, "price_list": price_list, "selling": 1},
					"price_list_rate",
				)
				rate = flt(price_list_rate)

			# Call ERPNext pricing rule engine
			rule_args = {
				"item_code": item_code,
				"qty": qty,
				"rate": rate,
				"price_list": price_list,
				"customer": customer_name,
				"company": company,
				"transaction_date": today(),
				"doctype": "Sales Order",
			}

			try:
				from erpnext.accounts.doctype.pricing_rule.pricing_rule import (
					get_pricing_rule_for_item,
				)

				pricing_rule_result = get_pricing_rule_for_item(rule_args)
			except Exception:
				pricing_rule_result = {}

			discount_percentage = flt(pricing_rule_result.get("discount_percentage", 0))
			discount_amount = flt(pricing_rule_result.get("discount_amount", 0))
			pricing_rule_name = pricing_rule_result.get("pricing_rule")

			if discount_percentage > 0:
				discount_amount = (rate * discount_percentage) / 100.0

			final_rate = max(0, rate - discount_amount)
			item_total_discount = discount_amount * qty
			total_discount += item_total_discount

			evaluated_items.append({
				"sku": sku,
				"item_code": item_code,
				"qty": qty,
				"original_rate": rate,
				"final_rate": final_rate,
				"discount_per_unit": discount_amount,
				"total_discount": item_total_discount,
				"pricing_rule": pricing_rule_name,
			})

		return {
			"status": "success",
			"items": evaluated_items,
			"total_cart_discount": total_discount,
		}

	except Exception as e:
		frappe.log_error("Calculate Cart Price API Error", frappe.get_traceback())
		return {"status": "error", "message": str(e)}
