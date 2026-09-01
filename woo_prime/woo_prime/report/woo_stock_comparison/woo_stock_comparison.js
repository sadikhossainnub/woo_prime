// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.query_reports["WooCommerce vs ERPNext Stock Comparison"] = {
	filters: [
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "show_only_mismatch",
			label: __("Show Only Mismatches"),
			fieldtype: "Check",
			default: 0,
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "status") {
			if (data.status === "Mismatch") {
				value = `<span style="color:red; font-weight:bold;">${value}</span>`;
			} else if (data.status === "Matched") {
				value = `<span style="color:green; font-weight:bold;">${value}</span>`;
			}
		}

		return value;
	},
};
