// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {};

const orig_onload = frappe.listview_settings["Item"].onload;

frappe.listview_settings["Item"].onload = function (listview) {
	if (orig_onload) {
		orig_onload.call(this, listview);
	}

	// Add Fetch Woo Items button directly on ERPNext Item Master List View
	listview.page.add_inner_button(__("Fetch Woo Items"), function () {
		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
			args: { auto_create_missing: true },
			freeze: true,
			freeze_message: __("Fetching products from WooCommerce & auto-linking to Item Master..."),
			callback: function (r) {
				listview.refresh();
			},
		});
	}, __("WooCommerce"));

	listview.page.add_inner_button(__("Fetch by SKU"), function () {
		frappe.prompt(
			[
				{
					fieldname: "sku",
					fieldtype: "Data",
					label: __("WooCommerce SKU"),
					reqd: 1,
				},
				{
					fieldname: "auto_create_missing",
					fieldtype: "Check",
					label: __("Auto-create ERPNext Item if not matched"),
					default: 1,
				},
			],
			function (values) {
				frappe.call({
					method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_single_item_by_sku",
					args: {
						sku: values.sku,
						auto_create_missing: values.auto_create_missing,
					},
					freeze: true,
					freeze_message: __("Fetching product from WooCommerce by SKU..."),
					callback: function (r) {
						if (r.message && r.message.status === "success") {
							frappe.msgprint({
								title: __("Fetched Successfully"),
								message: r.message.message,
								indicator: "green",
							});
							listview.refresh();
						}
					},
				});
			},
			__("Fetch Single Product from WooCommerce"),
			__("Fetch")
		);
	}, __("WooCommerce"));

	listview.page.add_inner_button(__("Auto Link Woo Items by SKU"), function () {
		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_item.woo_item.auto_link_unlinked_items",
			freeze: true,
			freeze_message: __("Auto-linking Woo Items to Item Master by SKU..."),
			callback: function (r) {
				listview.refresh();
			},
		});
	}, __("WooCommerce"));
};
