// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.is_new()) {
			// --- Publish to WooCommerce button ---
			frm.add_custom_button(
				__("Publish to WooCommerce"),
				function () {
					frappe.confirm(
						__("Publish <b>{0}</b> to WooCommerce in the background?<br><br>If a linked Woo Item does not exist, one will be auto-created.", [frm.doc.item_name || frm.doc.name]),
						function () {
							frappe.call({
								method: "woo_prime.woo_prime.doctype.woo_item.woo_item.publish_item_from_item_master",
								args: { item_code: frm.doc.name, background: true },
								callback: function (r) {
									if (r && r.message) {
										frappe.show_alert({
											message: r.message.message || __("Started background publish to WooCommerce..."),
											indicator: "blue",
										}, 5);
									}
								},
							});
						}
					);
				},
				__("WooCommerce")
			);

			// Realtime listener for background publish progress
			if (!frm.woo_publish_listener_attached) {
				frm.woo_publish_listener_attached = true;
				frappe.realtime.on("woo_publish_progress", function (data) {
					if (data.operation === "publish_item" && data.item_code === frm.doc.name) {
						if (data.status === "completed") {
							let msg = __("✅ Published to WooCommerce!<br>Product ID: <b>{0}</b>", [data.woo_product_id]);
							if (data.woo_product_url) {
								msg += __('<br><a href="{0}" target="_blank">View on WooCommerce ↗</a>', [data.woo_product_url]);
							}
							if (data.woo_item_name) {
								msg += __('<br><br><a href="/app/woo-item/{0}">Open Woo Item →</a>', [data.woo_item_name]);
							}
							frappe.msgprint({
								message: msg,
								title: __("Published"),
								indicator: "green",
							});
							frm.reload_doc();
						} else if (data.status === "error") {
							frappe.msgprint({
								message: data.message || __("Failed to publish to WooCommerce."),
								title: __("Publish Failed"),
								indicator: "red",
							});
							frm.reload_doc();
						}
					}
				});
			}

			// --- Fetch & Link WooCommerce Items button ---
			frm.add_custom_button(
				__("Fetch & Link WooCommerce Items"),
				function () {
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
						args: { auto_create_missing: true, batch_size: 10, background: true },
						callback: function (r) {
							if (r && r.message) {
								frappe.show_alert({
									message: r.message.message || __("Started background product fetch (10 items per batch)..."),
									indicator: "blue",
								});
							}
						},
					});
				},
				__("WooCommerce")
			);
		}
	},
});
