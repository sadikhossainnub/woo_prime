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
		// 1. Fetch Products from WooCommerce
		listview.page.add_inner_button(__("Fetch from WooCommerce"), function () {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
				freeze: true,
				freeze_message: __("Fetching products from WooCommerce & auto-linking by SKU..."),
				callback: function (r) {
					listview.refresh();
				},
			});
		});

		// 2. Auto Link Unlinked Items by SKU
		listview.page.add_inner_button(__("Auto Link by SKU"), function () {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.auto_link_unlinked_items",
				freeze: true,
				freeze_message: __("Auto-linking Woo Items to ERPNext Items by SKU..."),
				callback: function (r) {
					listview.refresh();
				},
			});
		});

		// 3. Action Item: Bulk Publish
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

		// 4. Action Item: Bulk Link to ERPNext Item
		listview.page.add_action_item(__("Bulk Link to ERPNext Item"), function () {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__("Please select Woo Items to link."));
				return;
			}

			const item_names = selected.map((d) => d.name);

			frappe.prompt(
				[
					{
						fieldtype: "Link",
						fieldname: "target_item_code",
						label: __("ERPNext Item Code (Leave empty to Auto-Match by SKU)"),
						options: "Item",
					},
				],
				function (values) {
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.bulk_link_to_erpnext_item",
						args: {
							items: item_names,
							target_item_code: values.target_item_code || null,
						},
						freeze: true,
						freeze_message: __("Linking Woo Items to ERPNext..."),
						callback: function (r) {
							if (r && r.message !== undefined) {
								frappe.show_alert({
									message: __("Successfully linked {0} item(s)!", [r.message]),
									indicator: "green",
								});
								listview.refresh();
							}
						},
					});
				},
				__("Link Woo Items to ERPNext Item"),
				__("Link Items")
			);
		});

		// 5. Action Item: Create ERPNext Items for Selected Woo Items
		listview.page.add_action_item(__("Create ERPNext Items for Selected"), function () {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__("Please select Woo Items to create ERPNext items for."));
				return;
			}

			const item_names = selected.map((d) => d.name);

			frappe.confirm(
				__("Auto-create ERPNext Item records for {0} selected item(s)?", [item_names.length]),
				function () {
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.create_erpnext_items_from_woo",
						args: { items: item_names },
						freeze: true,
						freeze_message: __("Creating ERPNext Items..."),
						callback: function (r) {
							listview.refresh();
						},
					});
				}
			);
		});
	},
};
