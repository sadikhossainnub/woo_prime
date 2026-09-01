// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.query_reports["WooCommerce Sales Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "woo_order_status",
			label: __("WooCommerce Status"),
			fieldtype: "Select",
			options: "\npending\nprocessing\non-hold\ncompleted\ncancelled\nrefunded\nfailed",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
	],
};
