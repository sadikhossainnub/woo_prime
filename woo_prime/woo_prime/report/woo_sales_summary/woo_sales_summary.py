# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate, today, add_months


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
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 140,
		},
		{
			"label": _("Item Name"),
			"fieldname": "item_name",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("Total Qty Sold"),
			"fieldname": "total_qty",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("Total Revenue"),
			"fieldname": "total_amount",
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"label": _("Order Count"),
			"fieldname": "order_count",
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"label": _("Avg Order Value"),
			"fieldname": "avg_rate",
			"fieldtype": "Currency",
			"width": 130,
		},
	]


def get_data(filters):
	conditions = get_conditions(filters)

	query = f"""
		SELECT
			soi.item_code,
			soi.item_name,
			SUM(soi.qty) AS total_qty,
			SUM(soi.amount) AS total_amount,
			COUNT(DISTINCT so.name) AS order_count,
			SUM(soi.amount) / NULLIF(COUNT(DISTINCT so.name), 0) AS avg_rate
		FROM `tabSales Order` so
		JOIN `tabSales Order Item` soi ON soi.parent = so.name
		WHERE so.docstatus = 1
			AND (so.woo_order_id IS NOT NULL AND so.woo_order_id != '')
			{conditions}
		GROUP BY soi.item_code, soi.item_name
		ORDER BY total_amount DESC
	"""

	return frappe.db.sql(query, filters, as_dict=True)


def get_conditions(filters):
	conditions = []

	if filters.get("from_date"):
		conditions.append("AND so.transaction_date >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("AND so.transaction_date <= %(to_date)s")
	if filters.get("woo_order_status"):
		conditions.append("AND so.woo_order_status = %(woo_order_status)s")
	if filters.get("customer"):
		conditions.append("AND so.customer = %(customer)s")

	return " ".join(conditions)


def get_chart(data):
	if not data:
		return None

	top_10 = data[:10]
	labels = [d.item_code or d.item_name for d in top_10]
	values = [flt(d.total_amount) for d in top_10]

	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Revenue"), "values": values}],
		},
		"type": "bar",
		"colors": ["#4585f7"],
	}


def get_report_summary(data):
	if not data:
		return []

	total_revenue = sum(flt(d.total_amount) for d in data)
	total_qty = sum(flt(d.total_qty) for d in data)
	total_orders = sum(d.order_count for d in data)

	return [
		{"label": _("Total Revenue"), "value": total_revenue, "datatype": "Currency"},
		{"label": _("Total Items Sold"), "value": total_qty, "datatype": "Float"},
		{"label": _("Total Line Item Hits"), "value": total_orders, "datatype": "Int"},
	]
