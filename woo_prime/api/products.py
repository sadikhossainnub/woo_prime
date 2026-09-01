# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import json
import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_items_for_sync(start=0, limit=100, item_group=None, search=None, item_codes=None):
	"""Endpoint for WordPress plugin to fetch items live from ERPNext for selective sync."""
	try:
		settings = frappe.get_single("Woo Settings")

		filters = {
			"disabled": 0,
			"is_sales_item": 1,
			"has_variants": 0,
		}
		if item_group:
			filters["item_group"] = item_group

		if item_codes:
			if isinstance(item_codes, str):
				try:
					item_codes = json.loads(item_codes)
				except Exception:
					item_codes = [x.strip() for x in item_codes.split(",") if x.strip()]
			if isinstance(item_codes, list) and item_codes:
				filters["item_code"] = ["in", item_codes]

		or_filters = None
		if search:
			or_filters = [
				["item_code", "like", f"%{search}%"],
				["item_name", "like", f"%{search}%"],
			]

		items = frappe.get_all(
			"Item",
			filters=filters,
			or_filters=or_filters,
			fields=["name", "item_code", "item_name", "description", "item_group", "image", "standard_rate"],
			start=int(start),
			page_length=int(limit),
			order_by="creation desc",
		)

		result = []
		price_list = getattr(settings, "default_price_list", None) or "Standard Selling"
		warehouse = getattr(settings, "default_warehouse", None)

		for item in items:
			price = frappe.db.get_value(
				"Item Price",
				{"item_code": item.item_code, "price_list": price_list, "selling": 1},
				"price_list_rate",
			)
			if price is None:
				price = item.standard_rate or 0

			stock_qty = 0
			if warehouse:
				try:
					from erpnext.stock.utils import get_latest_stock_qty
					stock_qty = flt(get_latest_stock_qty(item.item_code, warehouse))
				except Exception:
					stock_qty = 0

			image_url = None
			if item.image:
				if item.image.startswith("http://") or item.image.startswith("https://"):
					image_url = item.image
				else:
					image_url = frappe.utils.get_url(item.image)

			result.append({
				"item_code": item.item_code,
				"sku": item.item_code,
				"item_name": item.item_name,
				"description": item.description or "",
				"item_group": item.item_group,
				"price": flt(price),
				"stock_quantity": flt(stock_qty),
				"image_url": image_url,
			})

		return {
			"status": "success",
			"count": len(result),
			"items": result,
		}
	except Exception as e:
		frappe.log_error("Get Items API Error", frappe.get_traceback())
		return {"status": "error", "message": str(e)}
