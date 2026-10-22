// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Item", {
	refresh(frm) {
		// Publish to WooCommerce button
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Publish to WooCommerce"),
				function () {
					let do_publish = function () {
						frappe.confirm(
							__("Publish <b>{0}</b> to WooCommerce in the background?", [frm.doc.item_name || frm.doc.name]),
							function () {
								frappe.call({
									method: "publish_to_woocommerce",
									doc: frm.doc,
									args: { background: true },
									callback: function (r) {
										if (r && r.message) {
											frappe.show_alert({
												message: r.message.message || __("Publishing to WooCommerce in background..."),
												indicator: "blue"
											}, 5);
										}
									}
								});
							}
						);
					};
					if (frm.is_dirty()) {
						frm.save().then(do_publish);
					} else {
						do_publish();
					}
				},
				__("Actions")
			);

			// Realtime listener for background publish progress
			if (!frm.publish_listener_attached) {
				frm.publish_listener_attached = true;
				frappe.realtime.on("woo_publish_progress", function (data) {
					if (data.operation === "publish_item" && data.woo_item_name === frm.doc.name) {
						if (data.status === "completed") {
							let msg = __("✅ Product published to WooCommerce successfully!<br>Product ID: <b>{0}</b>", [data.woo_product_id]);
							if (data.woo_product_url) {
								msg += __('<br><a href="{0}" target="_blank">View on WooCommerce ↗</a>', [data.woo_product_url]);
							}
							frappe.msgprint({
								title: __("Published"),
								message: msg,
								indicator: "green",
							});
							frm.reload_doc();
						} else if (data.status === "error") {
							frappe.msgprint({
								title: __("Publish Failed"),
								message: data.message || __("Failed to publish to WooCommerce."),
								indicator: "red",
							});
							frm.reload_doc();
						}
					}
				});
			}

			// Show sync buttons only if already published
			if (frm.doc.woo_product_id) {
				frm.add_custom_button(
					__("Sync Item Now"),
					function () {
						frappe.call({
							method: "sync_item_now",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Syncing Item to WooCommerce..."),
							timeout: 300,
							callback: function (r) {
								frm.reload_doc();
							},
						});
					},
					__("Actions")
				);

				frm.add_custom_button(
					__("Sync Stock"),
					function () {
						frappe.call({
							method: "sync_stock_now",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Syncing Stock..."),
							timeout: 300,
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
							timeout: 300,
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

			// Delete Woo Item (with optional cascade to ERPNext & WooCommerce)
			frm.add_custom_button(
				__("Delete Item"),
				function () {
					let d = new frappe.ui.Dialog({
						title: __("Delete Woo Item"),
						indicator: "red",
						fields: [
							{
								fieldtype: "HTML",
								fieldname: "info_html",
								options: `<div class="alert alert-warning" style="margin-bottom:10px;">
									<b>${__("Warning:")}</b>
									${__("This will permanently delete the Woo Item record.")}
									${frm.doc.woo_product_id
										? " " + __("If you also delete from WooCommerce, the product will be removed from your store.")
										: ""}
									${__("This action cannot be undone.")}
								</div>`,
							},
							{
								fieldname: "delete_from_woocommerce",
								label: __("Also delete from WooCommerce"),
								fieldtype: "Check",
								default: 0,
								description: frm.doc.woo_product_id
									? __("Product ID: {0}", [frm.doc.woo_product_id])
									: __("No WooCommerce product linked"),
							},
							{
								fieldname: "delete_item_master",
								label: __("Also delete from ERPNext Item Master"),
								fieldtype: "Check",
								default: 0,
								description: frm.doc.item_code
									? __("Item Code: {0}", [frm.doc.item_code])
									: __("No ERPNext Item linked"),
							},
						],
						primary_action_label: __("Delete"),
						primary_action: function (values) {
							d.hide();
							let confirm_lines = [
								__("Are you sure you want to delete <b>{0}</b>?", [frm.doc.item_name || frm.doc.name]),
							];
							if (values.delete_from_woocommerce) {
								confirm_lines.push("<br>✅ " + __("Will delete from WooCommerce"));
							}
							if (values.delete_item_master) {
								confirm_lines.push("<br>✅ " + __("Will delete from ERPNext Item Master"));
							}
							frappe.confirm(
								confirm_lines.join(""),
								function () {
									frappe.call({
										method: "woo_prime.woo_prime.doctype.woo_item.woo_item.delete_woo_item",
										args: {
											woo_item_name: frm.doc.name,
											delete_item_master: values.delete_item_master ? 1 : 0,
											delete_from_woocommerce: values.delete_from_woocommerce ? 1 : 0,
										},
										freeze: true,
										freeze_message: __("Deleting..."),
										callback: function (r) {
											if (r && r.message && r.message.status === "ok") {
												frappe.show_alert({
													message: r.message.message || __("Deleted successfully."),
													indicator: "green",
												}, 5);
												frappe.set_route("List", "Woo Item");
											}
										},
									});
								}
							);
						},
					});
					d.show();
				},
				__("Actions")
			);
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

		// Adjust stock management labels based on template / variant / simple product
		if (frm.doc.has_variants) {
			frm.set_df_property("manage_stock", "label", __("Track stock quantity on parent product (Inventory tab)"));
			frm.set_df_property("manage_variation_stock", "hidden", 0);
		} else if (frm.doc.variant_of) {
			frm.set_df_property("manage_stock", "label", __("Manage stock? (Variation level)"));
			frm.set_df_property("manage_variation_stock", "hidden", 1);
		} else {
			frm.set_df_property("manage_stock", "label", __("Track stock quantity for this product"));
			frm.set_df_property("manage_variation_stock", "hidden", 1);
		}

		// Render variants list and stock status for template items
		if (frm.doc.has_variants && frm.doc.item_code && frm.fields_dict["variants_html"]) {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_template_variants_info",
				args: { item_code: frm.doc.item_code },
				callback: function (r) {
					if (r && r.message && r.message.length) {
						let rows = r.message.map((v) => {
							let woo_badge = v.published
								? `<span class="indicator-pill green">#${v.woo_product_id || ""}</span>`
								: `<span class="indicator-pill gray">${__("Not Published")}</span>`;
							let manage_badge = v.manage_stock
								? `<span class="text-success font-weight-bold"><i class="fa fa-check"></i> ${__("Yes")}</span>`
								: `<span class="text-muted"><i class="fa fa-times"></i> ${__("No")}</span>`;
							let link = v.woo_item_name
								? `<a href="/desk/woo-item/${v.woo_item_name}">${v.sku || v.item_code}</a>`
								: (v.sku || v.item_code);
							return `<tr>
								<td>${link}</td>
								<td>${frappe.utils.escape_html(v.item_name || "")}</td>
								<td>${woo_badge}</td>
								<td><b>${v.stock_qty}</b></td>
								<td>${manage_badge}</td>
							</tr>`;
						}).join("");

						let html = `
							<table class="table table-bordered table-hover table-sm text-sm" style="margin-top:10px;">
								<thead>
									<tr class="text-muted bg-light">
										<th>${__("Variation / SKU")}</th>
										<th>${__("Item Name")}</th>
										<th>${__("WooCommerce ID")}</th>
										<th>${__("Stock Qty")}</th>
										<th>${__("Manage Stock?")}</th>
									</tr>
								</thead>
								<tbody>${rows}</tbody>
							</table>
						`;
						frm.fields_dict["variants_html"].$wrapper.html(html);
					} else if (frm.fields_dict["variants_html"] && frm.fields_dict["variants_html"].$wrapper) {
						frm.fields_dict["variants_html"].$wrapper.html(
							`<div class="text-muted text-sm" style="padding:10px 0;">${__("No active variants found for this template.")}</div>`
						);
					}
				},
			});
		}

		// Render woo_product_url as a clickable link (Small Text has no 140-char limit but no auto-link)
		if (frm.doc.woo_product_url && frm.fields_dict["woo_product_url"]) {
			let render_url = function () {
				let $wrapper = frm.fields_dict["woo_product_url"].$wrapper;
				if (!$wrapper) return;
				let url = frm.doc.woo_product_url;
				let $target = $wrapper.find(".control-value, .like-disabled-input");
				if ($target.length) {
					$target.html(
						`<a href="${frappe.utils.escape_html(url)}" target="_blank" rel="noopener noreferrer"
							style="word-break:break-all;">${frappe.utils.escape_html(url)}</a>`
					);
				}
			};
			render_url();
			frappe.after_ajax(render_url);
		}

		// Auto-fetch price if empty or 0
		if (frm.doc.item_code && (!frm.doc.regular_price || flt(frm.doc.regular_price) <= 0)) {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_item_price_rate",
				args: { item_code: frm.doc.item_code },
				callback: function (r) {
					if (r && r.message !== undefined && r.message !== null && flt(r.message) > 0) {
						if (!frm.doc.regular_price || flt(frm.doc.regular_price) <= 0) {
							frm.set_value("regular_price", flt(r.message));
						}
					}
				},
			});
		}
	},

	item_code(frm) {
		if (frm.doc.item_code) {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_item_price_rate",
				args: { item_code: frm.doc.item_code },
				callback: function (r) {
					if (r && r.message !== undefined && r.message !== null && flt(r.message) > 0) {
						frm.set_value("regular_price", flt(r.message));
					}
				},
			});
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

		// 3. Action Item: Bulk Publish / Sync to WooCommerce
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

