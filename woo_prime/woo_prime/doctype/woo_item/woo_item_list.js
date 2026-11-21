// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.listview_settings["Woo Item"] = {
	add_fields: ["sync_status", "published", "woo_product_id", "image"],
	get_indicator: function (doc) {
		if (doc.sync_status === "Synced") {
			return [__("Synced"), "green", "sync_status,=,Synced"];
		} else if (doc.sync_status === "Partial") {
			return [__("Partial"), "orange", "sync_status,=,Partial"];
		} else if (doc.sync_status === "Error") {
			return [__("Error"), "red", "sync_status,=,Error"];
		}
		return [__("Not Synced"), "grey", "sync_status,=,Not Synced"];
	},
	onload: function (listview) {
		// 1. Fetch Products from WooCommerce (Bulk)
		listview.page.add_inner_button(__("Fetch All from WooCommerce"), function () {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
				freeze: true,
				freeze_message: __("Fetching products from WooCommerce & auto-linking by SKU..."),
				callback: function (r) {
					listview.refresh();
				},
			});
		});

		// 1b. Fetch Single Item by SKU
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
						default: 0,
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
				__("Fetch Single Product by SKU"),
				__("Fetch")
			);
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

		// 3. Action Item: Bulk Sync / Publish to WooCommerce
		listview.page.add_action_item(__("Bulk Sync / Publish to WooCommerce"), function () {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__("Please select items to sync / publish."));
				return;
			}

			const item_names = selected.map((d) => d.name);

			frappe.confirm(
				__("Sync / Publish {0} item(s) to WooCommerce in the background?", [item_names.length]),
				function () {
					show_bulk_publish_dialog(listview, item_names.length);
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.bulk_publish",
						args: { items: item_names, background: true },
						callback: function (r) {
							if (r && r.message && r.message.status === "queued") {
								frappe.show_alert({
									message: r.message.message || __("Bulk sync queued in background..."),
									indicator: "blue"
								}, 5);
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

		// 6. Action Item: Update Regular Price from ERPNext
		listview.page.add_action_item(__("Update Prices from ERPNext"), function () {
			const selected = listview.get_checked_items();
			const item_names = selected.length ? selected.map((d) => d.name) : null;

			frappe.confirm(
				item_names
					? __("Update Regular Price from ERPNext Item Price for {0} selected item(s)?", [item_names.length])
					: __("Update Regular Price from ERPNext for all Woo Items with empty price?"),
				function () {
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.update_regular_prices",
						args: { items: item_names },
						freeze: true,
						freeze_message: __("Updating Prices from ERPNext..."),
						callback: function (r) {
							if (r && r.message !== undefined) {
								frappe.show_alert({
									message: __("Updated prices for {0} item(s)!", [r.message]),
									indicator: "green",
								});
								listview.refresh();
							}
						},
					});
				}
			);
		});
	},
};

// Bulk Publish progress dialog
let bulk_publish_dialog = null;
let bulk_publish_subscription = null;

function show_bulk_publish_dialog(listview, total_count) {
	if (bulk_publish_dialog) {
		bulk_publish_dialog.hide();
	}

	bulk_publish_dialog = new frappe.ui.Dialog({
		title: __("Publishing Items to WooCommerce"),
		indicator: "blue",
		fields: [
			{
				fieldname: "progress_html",
				fieldtype: "HTML",
			},
		],
		primary_action_label: __("Close"),
		primary_action: function () {
			bulk_publish_dialog.hide();
			if (bulk_publish_subscription) {
				frappe.realtime.off("woo_publish_progress", bulk_publish_subscription);
				bulk_publish_subscription = null;
			}
		},
	});

	let html = `
		<div class="woo-publish-progress">
			<div class="progress" style="height: 25px; margin-bottom: 15px;">
				<div class="progress-bar progress-bar-striped progress-bar-animated"
					role="progressbar"
					style="width: 10%;"
					id="woo-pub-bar">
				</div>
			</div>
			<p id="woo-pub-msg" style="font-size: 14px; color: #555;">
				<i class="fa fa-spinner fa-spin"></i> Initializing background publish for ${total_count} items...
			</p>
			<div class="row text-center" style="margin-top: 15px;">
				<div class="col-xs-4">
					<div class="well well-sm">
						<h4 id="woo-pub-total">${total_count}</h4>
						<small class="text-muted">Total</small>
					</div>
				</div>
				<div class="col-xs-4">
					<div class="well well-sm">
						<h4 id="woo-pub-success" style="color: #28a745;">0</h4>
						<small class="text-muted">Published</small>
					</div>
				</div>
				<div class="col-xs-4">
					<div class="well well-sm">
						<h4 id="woo-pub-failed" style="color: #dc3545;">0</h4>
						<small class="text-muted">Failed</small>
					</div>
				</div>
			</div>
		</div>
	`;

	bulk_publish_dialog.fields_dict.progress_html.$wrapper.html(html);
	bulk_publish_dialog.show();

	bulk_publish_subscription = function (data) {
		if (data.operation !== "bulk_publish") {
			return;
		}

		if (data.total) {
			$("#woo-pub-total").text(data.total);
		}
		if (data.success !== undefined) {
			$("#woo-pub-success").text(data.success);
		}
		if (data.failed !== undefined) {
			$("#woo-pub-failed").text(data.failed);
		}

		if (data.message) {
			let icon =
				data.status === "completed"
					? "fa-check-circle text-success"
					: data.status === "error"
					? "fa-exclamation-circle text-danger"
					: "fa-spinner fa-spin";
			$("#woo-pub-msg").html(`<i class="fa ${icon}"></i> ${data.message}`);
		}

		if (data.total && data.current) {
			let pct = Math.round((data.current / data.total) * 100);
			$("#woo-pub-bar").css("width", pct + "%").text(pct + "%");
		}

		if (data.status === "completed") {
			$("#woo-pub-bar")
				.css("width", "100%")
				.removeClass("progress-bar-animated progress-bar-striped")
				.addClass("bg-success");
			if (listview) {
				listview.refresh();
			}
			setTimeout(function () {
				if (bulk_publish_dialog) {
					bulk_publish_dialog.hide();
				}
			}, 4000);
		}
	};

	frappe.realtime.on("woo_publish_progress", bulk_publish_subscription);
}
