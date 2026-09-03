// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Category", {
	refresh(frm) {
		frm.add_custom_button(__("Push to WooCommerce"), function () {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_category.woo_category.push_category_to_woo",
				args: {
					doc_name: frm.doc.name,
				},
				freeze: true,
				freeze_message: __("Pushing Category to WooCommerce..."),
				callback: function (r) {
					frm.reload_doc();
				},
			});
		}).addClass("btn-primary");

		frm.add_custom_button(__("Sync Categories from WooCommerce"), function () {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo",
				freeze: true,
				freeze_message: __("Syncing Product Categories from WooCommerce..."),
				callback: function (r) {
					frm.refresh();
				},
			});
		});
	},
});
