# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, cint


def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	summary = get_report_summary(data)

	return columns, data, None, chart, summary


def get_columns():
	return [
		{
			"label": _("Woo Item"),
			"fieldname": "woo_item",
			"fieldtype": "Link",
			"options": "Woo Item",
			"width": 140,
		},
		{
			"label": _("SKU"),
			"fieldname": "sku",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("ERPNext Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"label": _("Item Name"),
			"fieldname": "item_name",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": _("Woo Product ID"),
			"fieldname": "woo_product_id",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": _("ERPNext Stock Qty"),
			"fieldname": "erp_qty",
			"fieldtype": "Float",
			"width": 140,
		},
		{
			"label": _("WooCommerce Stock Qty"),
			"fieldname": "woo_qty",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Difference"),
			"fieldname": "difference",
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"label": _("Status"),
			"fieldname": "status",
			"fieldtype": "Data",
			"width": 120,
		},
	]


def get_data(filters):
	settings = frappe.get_single("Woo Settings")
	default_warehouse = filters.get("warehouse") or settings.default_warehouse

	# Fetch all published Woo Items with ERPNext item details and stock
	woo_items = frappe.db.sql(
		"""
		SELECT
			wi.name AS woo_item,
			wi.sku,
			wi.item_code,
			wi.woo_product_id,
			item.item_name,
			COALESCE(SUM(bin.actual_qty), 0) AS erp_qty
		FROM `tabWoo Item` wi
		LEFT JOIN `tabItem` item ON item.name = wi.item_code
		LEFT JOIN `tabBin` bin ON bin.item_code = wi.item_code
			%s
		WHERE wi.published = 1
		GROUP BY wi.name, wi.sku, wi.item_code, wi.woo_product_id, item.item_name
	"""
		% ("AND bin.warehouse = %(warehouse)s" if default_warehouse else ""),
		{"warehouse": default_warehouse},
		as_dict=True,
	)

	if not woo_items:
		return []

	# Map WooCommerce live stock if WooCommerce connection is enabled
	woo_stock_map = {}
	if settings.enabled and settings.woo_site_url:
		try:
			from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

			api = get_woo_api()
			# Fetch product IDs in batches of 50
			product_ids = [str(i.woo_product_id) for i in woo_items if i.woo_product_id]
			if product_ids:
				for i in range(0, len(product_ids), 50):
					chunk = product_ids[i : i + 50]
					response = api.get("products", params={"include": ",".join(chunk), "per_page": 100})
					if response.status_code == 200:
						products = response.json()
						for p in products:
							woo_stock_map[str(p.get("id"))] = flt(p.get("stock_quantity") or 0)
		except Exception:
			frappe.log_error("WooCommerce Stock Fetching Failed in Report", frappe.get_traceback())

	result = []
	show_only_mismatch = cint(filters.get("show_only_mismatch", 0))

	for item in woo_items:
		woo_pid = str(item.woo_product_id or "")
		woo_qty = woo_stock_map.get(woo_pid, 0.0)
		erp_qty = flt(item.erp_qty)
		diff = erp_qty - woo_qty

		if diff == 0:
			status = "Matched"
		else:
			status = "Mismatch"

		if show_only_mismatch and status == "Matched":
			continue

		result.append({
			"woo_item": item.woo_item,
			"sku": item.sku,
			"item_code": item.item_code,
			"item_name": item.item_name,
			"woo_product_id": item.woo_product_id,
			"erp_qty": erp_qty,
			"woo_qty": woo_qty,
			"difference": diff,
			"status": status,
		})

	return result


def get_chart(data):
	if not data:
		return None

	matched_count = sum(1 for d in data if d["status"] == "Matched")
	mismatch_count = sum(1 for d in data if d["status"] == "Mismatch")

	return {
		"data": {
			"labels": [_("Matched"), _("Mismatch")],
			"datasets": [{"name": _("Stock Status"), "values": [matched_count, mismatch_count]}],
		},
		"type": "donut",
		"colors": ["#28a745", "#dc3545"],
	}


def get_report_summary(data):
	if not data:
		return []

	total_items = len(data)
	mismatch_count = sum(1 for d in data if d["status"] == "Mismatch")
	matched_count = total_items - mismatch_count

	return [
		{"label": _("Total Items"), "value": total_items, "datatype": "Int"},
		{"label": _("Stock Matched"), "value": matched_count, "datatype": "Int", "indicator": "green"},
		{"label": _("Stock Mismatched"), "value": mismatch_count, "datatype": "Int", "indicator": "red"},
	]
