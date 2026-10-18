// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.listview_settings["Woo Tag"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Fetch from WooCommerce"), function () {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_tag.woo_tag.fetch_tags_from_woo",
				freeze: true,
				freeze_message: __("Fetching tags from WooCommerce..."),
				callback: function (r) {
					if (r && r.message) {
						frappe.show_alert({
							message: r.message.message || __("Synced tags successfully!"),
							indicator: "green",
						});
					}
					listview.refresh();
				},
			});
		});
	},
};

