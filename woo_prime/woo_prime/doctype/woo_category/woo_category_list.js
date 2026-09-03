// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.listview_settings["Woo Category"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Fetch from WooCommerce"), function () {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo",
				freeze: true,
				freeze_message: __("Fetching product categories from WooCommerce..."),
				callback: function (r) {
					if (r && r.message) {
						frappe.show_alert({
							message: __("Synced {0} categories!", [r.message]),
							indicator: "green",
						});
					}
					listview.refresh();
				},
			});
		});

		listview.page.add_action_item(__("Push to WooCommerce"), function () {
			const checked_items = listview.get_checked_items();
			if (!checked_items.length) return;

			frappe.confirm(
				__("Are you sure you want to push {0} selected category(ies) to WooCommerce?", [checked_items.length]),
				function () {
					frappe.run_serially(
						checked_items.map((item) => {
							return () =>
								frappe.call({
									method: "woo_prime.woo_prime.doctype.woo_category.woo_category.push_category_to_woo",
									args: { doc_name: item.name },
								});
						})
					).then(() => {
						frappe.show_alert({
							message: __("Pushed categories to WooCommerce successfully!"),
							indicator: "green",
						});
						listview.refresh();
					});
				}
			);
		});
	},
};
