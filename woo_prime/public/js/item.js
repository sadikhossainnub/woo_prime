// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Fetch & Link WooCommerce Items"),
				function () {
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
						args: { auto_create_missing: true },
						freeze: true,
						freeze_message: __("Fetching products from WooCommerce & auto-linking to Item Master..."),
						callback: function (r) {
							frm.reload_doc();
						},
					});
				},
				__("WooCommerce")
			);
		}
	},
});
