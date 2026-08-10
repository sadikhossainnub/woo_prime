// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Item", {
	refresh(frm) {
		// Publish to WooCommerce button
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Publish to WooCommerce"),
				function () {
					frappe.call({
						method: "publish_to_woocommerce",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Publishing to WooCommerce..."),
						callback: function (r) {
							frm.reload_doc();
						},
					});
				},
				__("Actions")
			);

			// Show sync buttons only if already published
			if (frm.doc.woo_product_id) {
				frm.add_custom_button(
					__("Sync Stock"),
					function () {
						frappe.call({
							method: "sync_stock_now",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Syncing Stock..."),
							callback: function (r) {
								frm.reload_doc();
							},
						});
					},
					__("Actions")
				);

				frm.add_custom_button(
					__("Sync Price"),
					function () {
						frappe.call({
							method: "sync_price_now",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Syncing Price..."),
							callback: function (r) {
								frm.reload_doc();
							},
						});
					},
					__("Actions")
				);
			}

			// Open WooCommerce product URL
			if (frm.doc.woo_product_url) {
				frm.add_custom_button(
					__("View on WooCommerce"),
					function () {
						window.open(frm.doc.woo_product_url, "_blank");
					},
					__("Actions")
				);
			}
		}

		// Status indicator
		if (frm.doc.sync_status === "Synced") {
			frm.dashboard.set_headline(
				__('<span class="indicator-pill green">Synced with WooCommerce</span>')
			);
		} else if (frm.doc.sync_status === "Error") {
			frm.dashboard.set_headline(
				__('<span class="indicator-pill red">Sync Error - Check Logs</span>')
			);
		}
	},
});

// List View: Bulk Publish action
frappe.listview_settings["Woo Item"] = {
	add_fields: ["sync_status", "published", "woo_product_id"],
	get_indicator: function (doc) {
		if (doc.sync_status === "Synced") {
			return [__("Synced"), "green", "sync_status,=,Synced"];
		} else if (doc.sync_status === "Error") {
			return [__("Error"), "red", "sync_status,=,Error"];
		}
		return [__("Not Synced"), "grey", "sync_status,=,Not Synced"];
	},
	onload: function (listview) {
		listview.page.add_action_item(__("Bulk Publish to WooCommerce"), function () {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__("Please select items to publish."));
				return;
			}

			const item_names = selected.map((d) => d.name);

			frappe.confirm(
				__("Publish {0} item(s) to WooCommerce?", [item_names.length]),
				function () {
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.bulk_publish",
						args: { items: item_names },
						freeze: true,
						freeze_message: __("Publishing items..."),
						callback: function (r) {
							if (r.message) {
								frappe.msgprint(
									__(
										"✅ Published: {0}<br>❌ Failed: {1}",
										[r.message.success, r.message.failed]
									)
								);
								listview.refresh();
							}
						},
					});
				}
			);
		});
	},
};
