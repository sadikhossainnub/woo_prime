// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Category", {
	refresh(frm) {
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
