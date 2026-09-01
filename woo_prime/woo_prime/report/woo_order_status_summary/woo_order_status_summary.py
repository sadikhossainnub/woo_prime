# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, today, add_months


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
			"label": _("WooCommerce Status"),
			"fieldname": "woo_order_status",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": _("Order Count"),
			"fieldname": "order_count",
			"fieldtype": "Int",
			"width": 120,
		},
		{
			"label": _("Total Amount"),
			"fieldname": "total_amount",
			"fieldtype": "Currency",
			"width": 160,
		},
		{
			"label": _("Avg Order Amount"),
			"fieldname": "avg_amount",
			"fieldtype": "Currency",
			"width": 150,
		},
	]


def get_data(filters):
	conditions = []

	if filters.get("from_date"):
		conditions.append("AND transaction_date >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("AND transaction_date <= %(to_date)s")
	if filters.get("company"):
		conditions.append("AND company = %(company)s")

	cond_str = " ".join(conditions)

	query = f"""
		SELECT
			COALESCE(woo_order_status, 'Unknown') AS woo_order_status,
			COUNT(name) AS order_count,
			SUM(grand_total) AS total_amount,
			AVG(grand_total) AS avg_amount
		FROM `tabSales Order`
		WHERE docstatus = 1
			AND (woo_order_id IS NOT NULL AND woo_order_id != '')
			{cond_str}
		GROUP BY woo_order_status
		ORDER BY order_count DESC
	"""

	return frappe.db.sql(query, filters, as_dict=True)


def get_chart(data):
	if not data:
		return None

	labels = [d.woo_order_status for d in data]
	values = [d.order_count for d in data]

	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Order Count"), "values": values}],
		},
		"type": "donut",
	}


def get_report_summary(data):
	if not data:
		return []

	total_orders = sum(d.order_count for d in data)
	total_revenue = sum(flt(d.total_amount) for d in data)
	avg_val = total_revenue / total_orders if total_orders else 0

	return [
		{"label": _("Total Orders"), "value": total_orders, "datatype": "Int"},
		{"label": _("Total Revenue"), "value": total_revenue, "datatype": "Currency"},
		{"label": _("Overall Avg Order Value"), "value": avg_val, "datatype": "Currency"},
	]
