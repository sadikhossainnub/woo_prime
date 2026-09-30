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
						__("Publish <b>{0}</b> to WooCommerce?<br><br>If a linked Woo Item does not exist, one will be auto-created.", [frm.doc.item_name || frm.doc.name]),
						function () {
							frappe.call({
								method: "woo_prime.woo_prime.doctype.woo_item.woo_item.publish_item_from_item_master",
								args: { item_code: frm.doc.name },
								freeze: true,
								freeze_message: __("Publishing to WooCommerce..."),
								callback: function (r) {
									if (r.message) {
										let d = r.message;
										let msg = __("✅ Published to WooCommerce!<br>Product ID: <b>{0}</b>", [d.woo_product_id]);
										if (d.woo_product_url) {
											msg += __('<br><a href="{0}" target="_blank">View on WooCommerce ↗</a>', [d.woo_product_url]);
										}
										if (d.woo_item_name) {
											msg += __('<br><br><a href="/app/woo-item/{0}">Open Woo Item →</a>', [d.woo_item_name]);
										}
										frappe.msgprint({
											message: msg,
											title: __("Published"),
											indicator: "green",
										});
									}
									frm.reload_doc();
								},
								error: function () {
									frm.reload_doc();
								},
							});
						}
					);
				},
				__("WooCommerce")
			);

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
