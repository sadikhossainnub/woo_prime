// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Item", {
	refresh(frm) {
		// Publish button
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Publish"),
				function () {
					if (frm.doc.has_variants && frm.has_included_variants === false) {
						frappe.msgprint({
							title: __("Cannot Publish Template"),
							message: __("Cannot publish template item: No variants are set to 'With Template'. At least one variant must be included in the template to publish a variable product."),
							indicator: "red"
						});
						return;
					}

					let proceed = function () {
						frappe.call({
							method: "woo_prime.woo_prime.doctype.woo_item.woo_item.publish_single_item",
							args: {
								woo_item_name: frm.doc.name,
								background: false,
								sync_images: 1
							},
							freeze: true,
							freeze_message: __("Publishing to WooCommerce..."),
							callback: function (r) {
								if (r && r.message) {
									frm.reload_doc();
								}
							}
						});
					};

					if (frm.is_dirty()) {
						frm.save().then(proceed);
					} else {
						proceed();
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
						} else if (data.status === "warning" || data.status === "partial") {
							// Bug 4 fix: surface Partial publish outcome to the user
							let warnMsg = data.message || __("Product partially synced — some variants may not have been pushed. Check Woo Sync Log for details.");
							frappe.msgprint({
								title: __("Partially Synced"),
								message: warnMsg,
								indicator: "orange",
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

			frm.add_custom_button(
				__("Fetch by SKU from Woo"),
				function () {
					let default_sku = frm.doc.sku || frm.doc.item_code || "";
					frappe.prompt(
						[
							{
								fieldname: "sku",
								fieldtype: "Data",
								label: __("WooCommerce SKU"),
								default: default_sku,
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
										frm.reload_doc();
									}
								},
							});
						},
						__("Fetch Product by SKU"),
						__("Fetch")
					);
				},
				__("Actions")
			);

			frm.add_custom_button(
				__("Fetch Price from ERPNext"),
				function () {
					fetch_price_from_erpnext_handler(frm);
				},
				__("Actions")
			);

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

			// Fetch / Reload attributes from ERPNext
			frm.add_custom_button(
				__("Fetch Attributes from ERPNext"),
				function () {
					let has_existing = (frm.doc.item_attributes || []).length > 0;
					let do_fetch = function (overwrite) {
						frappe.call({
							method: "populate_attributes_from_erpnext",
							doc: frm.doc,
							args: { overwrite: overwrite ? 1 : 0 },
							freeze: true,
							freeze_message: __("Fetching attributes from ERPNext..."),
							callback: function (r) {
								if (r && r.message && r.message.status === "success") {
									frappe.show_alert({
										message: __("Loaded {0} attribute(s) from ERPNext", [r.message.count]),
										indicator: "green",
									}, 4);
									frm.reload_doc();
								}
							},
						});
					};

					if (has_existing) {
						frappe.confirm(
							__("Overwrite existing attributes with ERPNext values, or only append missing ones?"),
							function () { do_fetch(true); }, // Yes -> Overwrite
							function () { do_fetch(false); } // No -> Merge
						);
					} else {
						do_fetch(false);
					}
				},
				__("Actions")
			);

			// Reload / Format Description from ERPNext Item
			frm.add_custom_button(
				__("Reload Description from Item"),
				function () {
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fix_existing_table_descriptions",
						args: { woo_item_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Formatting description tables..."),
						callback: function (r) {
							if (r && r.message && r.message.status === "success") {
								frappe.show_alert({
									message: __("Description table formatted successfully!"),
									indicator: "green",
								});
								frm.reload_doc();
							}
						},
					});
				},
				__("Actions")
			);

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
								default: 1,
								description: frm.doc.woo_product_id
									? __("Product ID: {0}", [frm.doc.woo_product_id])
									: __("No WooCommerce product linked"),
							},
							{
								fieldname: "delete_item_master",
								label: __("Also delete from ERPNext Item Master"),
								fieldtype: "Check",
								default: 1,
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
		} else if (frm.doc.sync_status === "Partial") {
			frm.dashboard.set_headline(
				__('<span class="indicator-pill orange">Partially Synced — Check Woo Sync Log</span>')
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

		// Render interactive variants management table for template items
		if (frm.doc.has_variants && frm.doc.item_code && frm.fields_dict["variants_html"]) {
			render_interactive_variants_table(frm);
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

		// Auto-fetch regular_price and sale_price if empty
		if (frm.doc.item_code && (!frm.doc.regular_price || flt(frm.doc.regular_price) <= 0 || (!frm.doc.sale_price && frm.doc.__islocal))) {
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_item_price_rate",
				args: { item_code: frm.doc.item_code },
				callback: function (r) {
					if (r && r.message) {
						let res = r.message;
						let reg_p = typeof res === "object" ? (res.regular_price !== undefined ? res.regular_price : res.rate) : res;
						let sale_p = typeof res === "object" ? res.sale_price : null;
						if (flt(reg_p) > 0 && (!frm.doc.regular_price || flt(frm.doc.regular_price) <= 0)) {
							frm.set_value("regular_price", flt(reg_p));
						}
						if (sale_p && flt(sale_p) > 0) {
							frm.set_value("sale_price", flt(sale_p));
						}
					}
				},
			});
		}

		// Initialize Multi-Select Dropdown for Product Attributes table
		_setup_attributes_multiselect(frm);

		// Render Image Preview & Child Table Gallery Formatter
		render_image_preview(frm);
		_setup_gallery_grid_formatter(frm);
	},

	image(frm) {
		render_image_preview(frm);
	},

	item_code(frm) {
		if (frm.doc.item_code) {
			setTimeout(() => {
				render_image_preview(frm);
			}, 600);
			// Auto-fill item_name from Item Master only if item_name is currently empty
			if (!frm.doc.item_name) {
				frappe.db.get_value("Item", frm.doc.item_code, "item_name", function (r) {
					if (r && r.item_name && !frm.doc.item_name) {
						frm.set_value("item_name", r.item_name);
					}
				});
			}

			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_item_price_rate",
				args: { item_code: frm.doc.item_code },
				callback: function (r) {
					if (r && r.message) {
						let res = r.message;
						let reg_p = typeof res === "object" ? (res.regular_price !== undefined ? res.regular_price : res.rate) : res;
						let sale_p = typeof res === "object" ? res.sale_price : 0;
						if (flt(reg_p) > 0) {
							frm.set_value("regular_price", flt(reg_p));
						}
						frm.set_value("sale_price", flt(sale_p));
					}
				},
			});

			// Auto-fill description from Item Master if empty
			if (!frm.doc.woo_description) {
				frappe.db.get_value("Item", frm.doc.item_code, "description", function (r) {
					if (r && r.description) {
						let clean_desc = _normalize_table_html(r.description);
						frm.set_value("woo_description", clean_desc);
					}
				});
			}

			// If item_attributes table is currently empty, auto-populate from ERPNext
			if (!frm.doc.item_attributes || frm.doc.item_attributes.length === 0) {
				frappe.call({
					method: "populate_attributes_from_erpnext",
					doc: frm.doc,
					args: { overwrite: 0 },
					callback: function (r) {
						if (r && r.message && r.message.status === "success") {
							frm.refresh_field("item_attributes");
							_setup_attributes_multiselect(frm);
						}
					},
				});
			}
		}
	},

});

function fetch_price_from_erpnext_handler(frm) {
	if (!frm.doc.item_code) {
		frappe.msgprint(__("Please select an Item Code first."));
		return;
	}
	frappe.call({
		method: "fetch_price_from_erpnext",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Fetching prices and pricing rules from ERPNext..."),
		callback: function (r) {
			if (r && r.message) {
				let res = r.message;
				let reg_p = flt(res.regular_price || 0);
				let sale_p = flt(res.sale_price || 0);

				frm.set_value("regular_price", reg_p);
				frm.set_value("sale_price", sale_p);

				let rule_info = res.pricing_rule
					? `<br><b>🏷️ Pricing Rule Applied:</b> ${res.pricing_rule}`
					: (sale_p > 0 ? "" : "<br><small class='text-muted'>ℹ️ No active discount Pricing Rule found for this item.</small>");

				frappe.msgprint({
					title: __("Prices Fetched from ERPNext"),
					indicator: "green",
					message: __("<b>Regular Price:</b> {0}<br><b>Sale Price:</b> {1}{2}", [
						reg_p,
						sale_p,
						rule_info
					])
				});
			}
		}
	});
}

// Event handlers for Woo Item Attribute child table
frappe.ui.form.on("Woo Item Attribute", {
	attribute(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.attribute) {
			if (!row.attribute_name) {
				frappe.model.set_value(cdt, cdn, "attribute_name", row.attribute);
			}
			if (row.is_variation === undefined || row.is_variation === null) {
				frappe.model.set_value(cdt, cdn, "is_variation", frm.doc.has_variants ? 1 : 0);
			}
			if (row.is_visible === undefined || row.is_visible === null) {
				frappe.model.set_value(cdt, cdn, "is_visible", 1);
			}

			// Fetch terms for this Woo Attribute
			frappe.call({
				method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_attribute_terms_for_woo_item",
				args: {
					woo_attribute: row.attribute,
					item_code: frm.doc.item_code || null,
				},
				callback: function (r) {
					if (r && r.message && r.message.terms && r.message.terms.length) {
						let terms = r.message.terms;
						frm._attribute_terms_cache = frm._attribute_terms_cache || {};
						frm._attribute_terms_cache[row.attribute] = terms;
						_update_multiselect_terms(frm, row.attribute, terms);

						// If options is empty, auto-fill with terms
						if (!row.options) {
							frappe.model.set_value(cdt, cdn, "options", terms.join(", "));
						}
					}
				},
			});
		}
	},

	btn_select_values(frm, cdt, cdn) {
		let grid_row = frm.get_field("item_attributes") && frm.get_field("item_attributes").grid.get_row(cdn);
		let $btn = grid_row ? grid_row.$row.find("[data-fieldname='btn_select_values']") : null;
		_open_attribute_multiselect_dropdown(frm, cdt, cdn, $btn, true);
	},

	form_render(frm, cdt, cdn) {
		let grid_row = frm.get_field("item_attributes") && frm.get_field("item_attributes").grid.get_row(cdn);
		if (grid_row && grid_row.grid_form) {
			let $options_input = grid_row.grid_form.fields_dict.options && grid_row.grid_form.fields_dict.options.$input;
			if ($options_input && !$options_input.data("woo-multiselect-attached")) {
				$options_input.data("woo-multiselect-attached", true);
				$options_input.on("focus click", function () {
					_open_attribute_multiselect_dropdown(frm, cdt, cdn, $options_input);
				});
			}
		}
	},

	item_attributes_add(frm, cdt, cdn) {
		let is_var = frm.doc.has_variants ? 1 : 0;
		frappe.model.set_value(cdt, cdn, "is_variation", is_var);
		frappe.model.set_value(cdt, cdn, "is_visible", 1);
	},
});

function _inject_multiselect_styles() {
	if (document.getElementById("woo-multiselect-styles")) return;
	let style = document.createElement("style");
	style.id = "woo-multiselect-styles";
	style.innerHTML = `
		.woo-multiselect-dropdown {
			position: absolute;
			z-index: 1060;
			background: var(--card-bg, #ffffff);
			color: var(--text-color, #1f272e);
			border: 1px solid var(--border-color, #d1d8dd);
			border-radius: 8px;
			box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16);
			min-width: 290px;
			max-width: 420px;
			padding: 10px 12px;
			font-size: 12px;
			animation: fadeInFast 0.15s ease-in-out;
		}
		.woo-multiselect-dropdown .dropdown-header {
			display: flex;
			align-items: center;
			justify-content: space-between;
			border-bottom: 1px solid var(--border-color, #ebeff2);
			padding-bottom: 6px;
			margin-bottom: 8px;
		}
		.woo-multiselect-dropdown .term-search-box {
			font-size: 11px;
			height: 28px;
			padding: 4px 8px;
			border-radius: 4px;
			border: 1px solid var(--border-color, #d1d8dd);
			width: 100%;
			margin-bottom: 8px;
		}
		.woo-multiselect-dropdown .terms-scroll-area {
			max-height: 180px;
			overflow-y: auto;
			border: 1px solid var(--border-color, #ebeff2);
			border-radius: 6px;
			padding: 6px 8px;
			margin-bottom: 8px;
			background: var(--bg-light-gray, #f8f9fa);
		}
		.woo-multiselect-dropdown .term-label {
			display: flex;
			align-items: center;
			margin-bottom: 4px;
			cursor: pointer;
			font-size: 12px;
			font-weight: 500;
			padding: 3px 6px;
			border-radius: 4px;
			transition: background 0.1s;
		}
		.woo-multiselect-dropdown .term-label:hover {
			background: var(--fg-hover-color, #e9ecef);
		}
		.woo-multiselect-dropdown .term-label input {
			margin-right: 8px;
			cursor: pointer;
			accent-color: var(--primary, #171717);
		}
		.woo-multiselect-dropdown .dropdown-footer {
			display: flex;
			align-items: center;
			justify-content: space-between;
			border-top: 1px solid var(--border-color, #ebeff2);
			padding-top: 8px;
			gap: 6px;
		}
		.grid-row [data-fieldname="options"] input {
			background-image: url("data:image/svg+xml;utf8,<svg fill='%238d99a6' height='18' viewBox='0 0 24 24' width='18' xmlns='http://www.w3.org/2000/svg'><path d='M7 10l5 5 5-5z'/></svg>") !important;
			background-repeat: no-repeat !important;
			background-position: right 6px center !important;
			padding-right: 22px !important;
			cursor: pointer;
		}
	`;
	document.head.appendChild(style);
}

function _setup_attributes_multiselect(frm) {
	_inject_multiselect_styles();

	frm._attribute_terms_cache = frm._attribute_terms_cache || {};

	let attrs = (frm.doc.item_attributes || [])
		.map((r) => r.attribute)
		.filter(Boolean);

	if (attrs.length > 0) {
		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_all_attribute_terms_for_woo_item",
			args: {
				attributes: JSON.stringify(Array.from(new Set(attrs))),
				item_code: frm.doc.item_code || null,
			},
			callback: function (r) {
				if (r && r.message && r.message.attribute_terms) {
					Object.assign(frm._attribute_terms_cache, r.message.attribute_terms);
				}
			},
		});
	}

	let grid_field = frm.get_field("item_attributes");
	if (grid_field && grid_field.grid) {
		let df = grid_field.grid.get_docfield("options");
		if (df) {
			df.get_data = function () {
				let row = null;
				if (frm.cur_grid && frm.cur_grid.grid_row && frm.cur_grid.grid_row.doc) {
					row = frm.cur_grid.grid_row.doc;
				} else if (frappe.ui.form.get_open_grid_form()) {
					let gf = frappe.ui.form.get_open_grid_form();
					row = gf.doc;
				}
				if (row && row.attribute && frm._attribute_terms_cache && frm._attribute_terms_cache[row.attribute]) {
					return frm._attribute_terms_cache[row.attribute];
				}
				return [];
			};
		}

		let $wrapper = grid_field.$wrapper;
		if ($wrapper && !$wrapper.data("woo-multiselect-init")) {
			$wrapper.data("woo-multiselect-init", true);

			$wrapper.on("focus click", "input[data-fieldname='options']", function (e) {
				let $input = $(this);
				let $row = $input.closest(".grid-row");
				let docname = $row.attr("data-name");
				if (!docname) return;
				let row = (frm.doc.item_attributes || []).find((r) => r.name === docname);
				if (row) {
					_open_attribute_multiselect_dropdown(frm, row.doctype, row.name, $input);
				}
			});

			$wrapper.on("click", ".grid-row .col[data-fieldname='options']", function (e) {
				if ($(e.target).closest(".woo-multiselect-dropdown, input").length) return;
				let $col = $(this);
				let $row = $col.closest(".grid-row");
				let docname = $row.attr("data-name");
				if (!docname) return;
				let row = (frm.doc.item_attributes || []).find((r) => r.name === docname);
				if (row) {
					setTimeout(() => {
						let $input = $col.find("input[data-fieldname='options']");
						_open_attribute_multiselect_dropdown(frm, row.doctype, row.name, $input.length ? $input : $col);
					}, 50);
				}
			});
		}
	}
}

function _open_attribute_multiselect_dropdown(frm, cdt, cdn, $target_elem, is_button) {
	let row = locals[cdt] && locals[cdt][cdn];
	if (!row) return;

	if (!row.attribute) {
		frappe.show_alert(
			{
				message: __("Please select a Woo Attribute first."),
				indicator: "orange",
			},
			3
		);
		return;
	}

	$("#woo-multiselect-dropdown").remove();

	let render_dropdown = function (terms) {
		let current_vals = (row.options || "")
			.replace(/\n/g, ",")
			.split(",")
			.map((v) => v.trim())
			.filter(Boolean);

		let all_terms = Array.from(new Set([...terms, ...current_vals])).sort((a, b) =>
			a.localeCompare(undefined, { numeric: true, sensitivity: "base" })
		);

		let selected_set = new Set(current_vals);

		let $dropdown = $(`
			<div id="woo-multiselect-dropdown" class="woo-multiselect-dropdown">
				<div class="dropdown-header">
					<div class="d-flex align-items-center" style="gap: 6px;">
						<i class="fa fa-list text-primary"></i>
						<strong>${frappe.utils.escape_html(row.attribute)}</strong>
						<span class="badge badge-info text-xs selected-count">${selected_set.size} selected</span>
					</div>
					<button type="button" class="btn btn-xs btn-link p-0 text-muted btn-close">&times;</button>
				</div>
				<div class="d-flex align-items-center justify-content-between mb-2" style="gap: 6px;">
					<div>
						<button type="button" class="btn btn-xs btn-default select-all-btn mr-1">Select All</button>
						<button type="button" class="btn btn-xs btn-default clear-all-btn">Clear All</button>
					</div>
					${all_terms.length > 5 ? `<input type="text" class="form-control term-search-box m-0" placeholder="Filter..." style="width: 120px;">` : ""}
				</div>
				<div class="terms-scroll-area">
					${
						all_terms.length === 0
							? `<div class="text-muted p-2 text-center text-xs">No predefined values found. Add one below.</div>`
							: all_terms
									.map(
										(t) => `
							<label class="term-label" data-term="${frappe.utils.escape_html(t).toLowerCase()}">
								<input type="checkbox" value="${frappe.utils.escape_html(t)}" ${selected_set.has(t) ? "checked" : ""}>
								<span class="term-text">${frappe.utils.escape_html(t)}</span>
							</label>
						`
									)
									.join("")
					}
				</div>
				<div class="dropdown-footer">
					<div class="input-group input-group-xs flex-grow-1 mr-2" style="max-width: 220px;">
						<input type="text" class="form-control add-custom-term-input" placeholder="Add new value..." style="font-size: 11px; height: 26px;">
						<div class="input-group-append">
							<button type="button" class="btn btn-xs btn-primary add-custom-term-btn">Add</button>
						</div>
					</div>
					<button type="button" class="btn btn-xs btn-primary btn-done">Done</button>
				</div>
			</div>
		`);

		$("body").append($dropdown);

		if ($target_elem && $target_elem.is(":visible")) {
			let offset = $target_elem.offset();
			let elem_h = $target_elem.outerHeight();
			let drop_w = $dropdown.outerWidth() || 320;
			let drop_h = $dropdown.outerHeight() || 280;

			let top = offset.top + elem_h + 4;
			let left = offset.left;

			if (left + drop_w > $(window).width() - 20) {
				left = Math.max(10, $(window).width() - drop_w - 20);
			}
			if (top + drop_h > $(window).height() + $(window).scrollTop() - 10) {
				top = Math.max(10, offset.top - drop_h - 4);
			}

			$dropdown.css({ top: top + "px", left: left + "px" });
		} else {
			$dropdown.css({
				position: "fixed",
				top: "50%",
				left: "50%",
				transform: "translate(-50%, -50%)",
				boxShadow: "0 12px 36px rgba(0,0,0,0.25)",
			});
		}

		let update_values = function () {
			let arr = Array.from(selected_set);
			let val_str = arr.join(", ");
			frappe.model.set_value(cdt, cdn, "options", val_str);
			if ($target_elem) {
				if ($target_elem.is("input")) {
					$target_elem.val(val_str).trigger("change");
				} else {
					let $inp = $target_elem.find("input");
					if ($inp.length) {
						$inp.val(val_str).trigger("change");
					}
				}
			}
			let grid_field = frm.get_field("item_attributes");
			if (grid_field && grid_field.grid) {
				grid_field.grid.refresh();
			}
			$dropdown.find(".selected-count").text(`${selected_set.size} selected`);
			frm.dirty();
		};

		$dropdown.on("change", ".term-label input", function () {
			let val = $(this).val();
			if (this.checked) {
				selected_set.add(val);
			} else {
				selected_set.delete(val);
			}
			update_values();
		});

		$dropdown.find(".select-all-btn").on("click", function () {
			$dropdown.find(".term-label:visible input").each(function () {
				this.checked = true;
				selected_set.add($(this).val());
			});
			update_values();
		});

		$dropdown.find(".clear-all-btn").on("click", function () {
			$dropdown.find(".term-label:visible input").each(function () {
				this.checked = false;
				selected_set.delete($(this).val());
			});
			update_values();
		});

		$dropdown.find(".term-search-box").on("input", function () {
			let q = $(this).val().toLowerCase().trim();
			$dropdown.find(".term-label").each(function () {
				let t = $(this).attr("data-term") || "";
				$(this).toggle(!q || t.includes(q));
			});
		});

		let handle_add_custom = function () {
			let $inp = $dropdown.find(".add-custom-term-input");
			let val = ($inp.val() || "").trim();
			if (!val) return;
			$inp.val("");
			if (!selected_set.has(val)) {
				selected_set.add(val);
				// Bug #10 fix: persist custom term into cache so it appears on next dropdown open
				if (row.attribute) {
					frm._attribute_terms_cache = frm._attribute_terms_cache || {};
					let cached = frm._attribute_terms_cache[row.attribute] || [];
					if (!cached.includes(val)) {
						frm._attribute_terms_cache[row.attribute] = [...cached, val].sort();
					}
				}
				let $existing = $dropdown.find(`.term-label input[value="${val}"]`);
				if ($existing.length) {
					$existing.prop("checked", true);
				} else {
					let $new_label = $(`
						<label class="term-label" data-term="${frappe.utils.escape_html(val).toLowerCase()}">
							<input type="checkbox" value="${frappe.utils.escape_html(val)}" checked>
							<span class="term-text">${frappe.utils.escape_html(val)}</span>
						</label>
					`);
					$dropdown.find(".terms-scroll-area").append($new_label);
				}
				update_values();
			}
		};

		$dropdown.find(".add-custom-term-btn").on("click", handle_add_custom);
		$dropdown.find(".add-custom-term-input").on("keypress", function (e) {
			if (e.which === 13) {
				e.preventDefault();
				handle_add_custom();
			}
		});

		let close_dropdown = function () {
			$dropdown.remove();
			$(document).off("mousedown.woo_multiselect keydown.woo_multiselect");
		};

		$dropdown.find(".btn-close, .btn-done").on("click", close_dropdown);

		setTimeout(() => {
			$(document).on("mousedown.woo_multiselect", function (e) {
				if (!$(e.target).closest("#woo-multiselect-dropdown").length && !$(e.target).is($target_elem)) {
					close_dropdown();
				}
			});
			$(document).on("keydown.woo_multiselect", function (e) {
				if (e.which === 27) {
					close_dropdown();
				}
			});
		}, 100);
	};

	let cached = frm._attribute_terms_cache && frm._attribute_terms_cache[row.attribute];
	if (cached && cached.length) {
		render_dropdown(cached);
	} else {
		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_attribute_terms_for_woo_item",
			args: {
				woo_attribute: row.attribute,
				item_code: frm.doc.item_code || null,
			},
			freeze: is_button,
			freeze_message: is_button ? __("Loading values...") : null,
			callback: function (r) {
				let terms = r && r.message && r.message.terms ? r.message.terms : [];
				frm._attribute_terms_cache = frm._attribute_terms_cache || {};
				frm._attribute_terms_cache[row.attribute] = terms;
				render_dropdown(terms);
			},
		});
	}
}

function _update_multiselect_terms(frm, attr_name, terms) {
	// Bug #9 fix: do NOT overwrite shared df.options (affects all rows).
	// Only update the per-attribute cache used by _open_attribute_multiselect_dropdown.
	if (!terms || !terms.length) return;
	try {
		frm._attribute_terms_cache = frm._attribute_terms_cache || {};
		frm._attribute_terms_cache[attr_name] = terms;
	} catch (e) {
		console.warn("Could not update attribute terms cache:", e);
	}
}

function _normalize_table_html(content) {
	if (!content || !content.includes("<table")) return content;
	let res = content.replace(/<\/?thead[^>]*>/gi, "").replace(/<\/?tfoot[^>]*>/gi, "");
	res = res.replace(/<th(\s[^>]*)?>(.*?)<\/th>/gis, function (match, attrs, inner) {
		attrs = attrs || "";
		if (/style=["']/i.test(attrs)) {
			attrs = attrs.replace(/style=["'](.*?)["']/i, function (m, style) {
				if (!/font-weight/i.test(style)) style += ";font-weight:bold";
				if (!/background/i.test(style)) style += ";background-color:#f8fafc";
				if (!/text-align/i.test(style)) style += ";text-align:center";
				return `style="${style}"`;
			});
		} else {
			attrs += ' style="font-weight:bold;background-color:#f8fafc;text-align:center;border:1px solid #cbd5e1;padding:7px 4px;"';
		}
		return `<td${attrs}><strong>${(inner || "").trim()}</strong></td>`;
	});
	res = res.replace(/(<table[^>]*>)(.*?)(<\/table>)/gis, function (m, open, body, close) {
		let body_clean = body.replace(/<\/?tbody[^>]*>/gi, "").trim();
		return `${open}<tbody>\n${body_clean}\n</tbody>${close}`;
	});
	return res;
}

// List View settings moved to woo_item_list.js


// ─────────────────────────────────────────────────────────────────────────────
// Child Table Handler & Image Preview Feature
// ─────────────────────────────────────────────────────────────────────────────

frappe.ui.form.on("Woo Item Image", {
	image(frm, cdt, cdn) {
		render_image_preview(frm);
	},
	caption(frm, cdt, cdn) {
		render_image_preview(frm);
	},
	images_add(frm) {
		render_image_preview(frm);
	},
	images_remove(frm) {
		render_image_preview(frm);
	}
});

function _setup_gallery_grid_formatter(frm) {
	if (!frm.fields_dict.images || !frm.fields_dict.images.grid) return;
	let grid = frm.fields_dict.images.grid;
	if (grid._woo_img_formatted) return;
	grid._woo_img_formatted = true;

	let docfield = (grid.docfields || []).find(df => df.fieldname === "image");
	if (docfield) {
		docfield.formatter = function (value) {
			if (!value) return "";
			return `<div style="display: flex; align-items: center; gap: 8px;">
				<img src="${encodeURI(value)}" style="width: 32px; height: 32px; object-fit: cover; border-radius: 4px; border: 1px solid var(--border-color, #d1d8dd); background: #fff;" onerror="this.style.display='none';">
				<span style="font-size: 11px; word-break: break-all;">${frappe.utils.escape_html(value)}</span>
			</div>`;
		};
	}
}

function render_image_preview(frm) {
	if (!frm.fields_dict["image_preview_html"]) return;
	let $wrapper = frm.fields_dict["image_preview_html"].$wrapper;
	if (!$wrapper) return;

	let main_image = frm.doc.image;
	let gallery = (frm.doc.images || []).filter(r => r && r.image);

	// If no images at all
	if (!main_image && gallery.length === 0) {
		$wrapper.html(`
			<div class="woo-image-preview-empty" style="
				border: 1px dashed var(--border-color, #d1d8dd);
				border-radius: 8px;
				padding: 18px 14px;
				text-align: center;
				background: var(--bg-light-gray, #fafbfc);
				margin: 6px 0 14px 0;
			">
				<div style="font-size: 26px; line-height: 1; margin-bottom: 6px; opacity: 0.65;">🖼️</div>
				<div style="font-weight: 500; font-size: 12px; color: var(--text-color, #1f272e);">
					${__("No Product Image Attached")}
				</div>
				<div style="font-size: 11px; color: var(--text-muted, #8d99a6); margin-top: 2px;">
					${__("Upload a Main Image above or select an Item Code to preview.")}
				</div>
			</div>
		`);
		return;
	}

	// Prepare array of all images
	let all_images = [];
	if (main_image) {
		all_images.push({
			url: main_image,
			label: __("Main Image"),
			is_main: true,
			caption: frm.doc.item_name || ""
		});
	}
	gallery.forEach((g, idx) => {
		all_images.push({
			url: g.image,
			label: __("Gallery #{0}", [idx + 1]),
			is_main: false,
			caption: g.caption || ""
		});
	});

	let active_idx = frm._active_preview_idx || 0;
	if (active_idx >= all_images.length) active_idx = 0;
	let active_img = all_images[active_idx];

	let count_label = all_images.length === 1
		? __("1 Image")
		: __("{0} Images ({1} Gallery)", [all_images.length, gallery.length]);

	let thumbs_html = "";
	if (all_images.length > 1) {
		thumbs_html = `
			<div class="woo-preview-thumbs-strip" style="
				display: flex;
				gap: 8px;
				overflow-x: auto;
				padding: 8px 2px 2px 2px;
				margin-top: 10px;
				border-top: 1px solid var(--border-color, #f0f1f3);
			">
				${all_images.map((img, idx) => `
					<div class="woo-preview-thumb-btn ${idx === active_idx ? 'active' : ''}" data-idx="${idx}" style="
						position: relative;
						flex: 0 0 52px;
						height: 52px;
						border-radius: 6px;
						border: ${idx === active_idx ? '2px solid var(--primary, #171717)' : '1px solid var(--border-color, #d1d8dd)'};
						box-shadow: ${idx === active_idx ? '0 0 0 2px rgba(0,0,0,0.1)' : 'none'};
						overflow: hidden;
						cursor: pointer;
						background: var(--bg-light-gray, #fff);
						transition: all 0.15s ease;
					" title="${frappe.utils.escape_html(img.label + (img.caption ? ': ' + img.caption : ''))}">
						<img src="${encodeURI(img.url)}" style="width: 100%; height: 100%; object-fit: cover;" onerror="this.src='/assets/frappe/images/default-avatar.png';">
						${img.is_main ? `<span style="position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,0.7); color: #fff; font-size: 8px; text-align: center; padding: 1px 0; font-weight: 600; text-transform: uppercase;">Main</span>` : ''}
					</div>
				`).join("")}
			</div>
		`;
	}

	let html = `
		<div class="woo-image-preview-card" style="
			border: 1px solid var(--border-color, #d1d8dd);
			border-radius: 8px;
			background: var(--card-bg, #ffffff);
			padding: 10px;
			margin: 6px 0 14px 0;
			box-shadow: 0 1px 3px rgba(0,0,0,0.03);
		">
			<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
				<div style="display: flex; align-items: center; gap: 6px;">
					<span style="font-weight: 600; font-size: 12px; color: var(--text-color, #1f272e);">
						🖼️ ${__("Image Preview")}
					</span>
					<span class="badge ${active_img.is_main ? 'badge-primary' : 'badge-default'}" style="font-size: 10px; font-weight: 500;">
						${active_img.label}
					</span>
					<span style="font-size: 10px; color: var(--text-muted, #8d99a6);">
						(${count_label})
					</span>
				</div>
				<div style="display: flex; gap: 5px;">
					<button type="button" class="btn btn-xs btn-default btn-preview-zoom" style="font-size: 11px; padding: 2px 7px;" title="${__("View Full Resolution in Lightbox")}">
						🔍 ${__("Zoom")}
					</button>
					<a href="${encodeURI(active_img.url)}" target="_blank" class="btn btn-xs btn-default" style="font-size: 11px; padding: 2px 7px;" title="${__("Open in New Tab")}">
						↗
					</a>
				</div>
			</div>

			<!-- Main Preview Display -->
			<div class="woo-preview-stage" style="
				position: relative;
				width: 100%;
				height: 220px;
				border-radius: 6px;
				background: var(--bg-light-gray, #f8f9fa);
				display: flex;
				align-items: center;
				justify-content: center;
				overflow: hidden;
				cursor: zoom-in;
				border: 1px solid var(--border-color, #f0f1f3);
			" title="${__("Click to Zoom / Expand")}">
				<img src="${encodeURI(active_img.url)}" style="
					max-width: 100%;
					max-height: 100%;
					object-fit: contain;
					transition: transform 0.2s ease;
				" onerror="this.onerror=null; this.src='/assets/frappe/images/default-avatar.png';">
			</div>

			${active_img.caption ? `
				<div style="font-size: 11px; color: var(--text-muted, #6c757d); margin-top: 5px; text-align: center; font-style: italic;">
					${frappe.utils.escape_html(active_img.caption)}
				</div>
			` : ''}

			${thumbs_html}
		</div>
	`;

	$wrapper.html(html);

	// Thumbnail click to switch preview
	$wrapper.find(".woo-preview-thumb-btn").on("click", function () {
		let idx = parseInt($(this).data("idx"));
		frm._active_preview_idx = idx;
		render_image_preview(frm);
	});

	// Zoom click handler -> Lightbox dialog
	$wrapper.find(".woo-preview-stage, .btn-preview-zoom").on("click", function (e) {
		e.stopPropagation();
		let img_url = active_img.url;
		let d = new frappe.ui.Dialog({
			title: active_img.label + (active_img.caption ? ` - ${active_img.caption}` : ''),
			size: "large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "lightbox_view",
					options: `
						<div style="text-align: center; padding: 10px; background: #121212; border-radius: 6px; overflow: auto; max-height: 75vh;">
							<img src="${encodeURI(img_url)}" style="max-width: 100%; max-height: 68vh; object-fit: contain; border-radius: 4px; box-shadow: 0 4px 16px rgba(0,0,0,0.5);">
							<div style="margin-top: 12px; display: flex; justify-content: center; gap: 8px;">
								<a href="${encodeURI(img_url)}" target="_blank" class="btn btn-xs btn-primary">${__("Open Original File ↗")}</a>
								<a href="${encodeURI(img_url)}" download class="btn btn-xs btn-default" style="background: #2a2a2a; color: #fff; border-color: #444;">${__("Download")}</a>
							</div>
						</div>
					`
				}
			]
		});
		d.show();
	});
}


function render_interactive_variants_table(frm) {
	if (!frm.doc.has_variants || !frm.doc.item_code || !frm.fields_dict["variants_html"]) {
		return;
	}

	let $wrapper = frm.fields_dict["variants_html"].$wrapper;
	if (!$wrapper) return;

	frappe.call({
		method: "woo_prime.woo_prime.doctype.woo_item.woo_item.get_template_variants_info",
		args: { item_code: frm.doc.item_code },
		callback: function (r) {
			let variants = (r && r.message) ? r.message : [];
			if (!variants.length) {
				frm.has_included_variants = false;
				$wrapper.html(`<div class="text-muted text-sm" style="padding:10px 0;">${__("No active variants found for this template.")}</div>`);
				return;
			}

			let included_variants = variants.filter(v => (v.mode || "With Template") === "With Template");
			frm.has_included_variants = included_variants.length > 0;

			// Attribute filters aggregation
			let attr_map = {};
			variants.forEach(v => {
				if (v.attributes) {
					Object.entries(v.attributes).forEach(([k, val]) => {
						if (!attr_map[k]) attr_map[k] = {};
						attr_map[k][val] = (attr_map[k][val] || 0) + 1;
					});
				}
			});

			let selected = new Set();

			function render() {
				let total = variants.length;
				let with_template = 0;
				let to_create = 0;
				let to_update = 0;
				let not_published = 0;
				let separate_groups = new Set();
				let separate_count = 0;

				variants.forEach(v => {
					let mode = v.mode || "With Template";
					if (mode === "With Template") {
						with_template++;
						if (v.woo_product_id) to_update++;
						else to_create++;
					} else if (mode === "Not Published") {
						not_published++;
					} else if (mode === "Separate Product") {
						separate_count++;
						if (v.group) separate_groups.add(v.group);
					}
				});

				let summary_html = `
					<div class="row mb-3 mt-1" style="background:#f8f9fa; border:1px solid #e2e8f0; border-radius:6px; padding:10px 12px; font-size:12px;">
						<div class="col-md-4">
							<span class="text-muted">${__("With Template")}:</span>
							<b>${with_template}</b>
							<span class="text-muted">(${to_update} ${__("update")}, ${to_create} ${__("create")})</span>
						</div>
						<div class="col-md-4">
							<span class="text-muted">${__("Not Published")}:</span>
							<b>${not_published}</b>
						</div>
						<div class="col-md-4">
							<span class="text-muted">${__("Separate Groups")}:</span>
							<b>${separate_count}</b>
							<span class="text-muted">(${__("across")} ${separate_groups.size} ${__("group(s)")})</span>
						</div>
					</div>
				`;

				let filter_pills_html = '';
				if (Object.keys(attr_map).length > 0) {
					let pills = [];
					Object.entries(attr_map).forEach(([attr, vals]) => {
						Object.entries(vals).forEach(([val, count]) => {
							pills.push(`
								<button type="button" class="btn btn-xs btn-default attr-select-pill mb-1 mr-1"
									data-attr="${frappe.utils.escape_html(attr)}"
									data-val="${frappe.utils.escape_html(val)}"
									style="border-radius:12px; padding:2px 10px; font-size:11px;">
									${frappe.utils.escape_html(attr)}: <b>${frappe.utils.escape_html(val)}</b> <span class="badge badge-light">${count}</span>
								</button>
							`);
						});
					});
					filter_pills_html = `
						<div class="attr-filters mb-2" style="display:flex; flex-wrap:wrap; align-items:center; gap:4px;">
							<span class="text-muted mr-1" style="font-size:11px;">${__("Quick Select")}:</span>
							${pills.join('')}
						</div>
					`;
				}

				let sel_count = selected.size;
				let toolbar_html = `
					<div class="variants-toolbar mb-2" style="display:flex; flex-wrap:wrap; gap:6px; align-items:center;">
						<button type="button" class="btn btn-xs btn-primary btn-action-include" ${sel_count === 0 ? "disabled" : ""}>
							<i class="fa fa-plus-circle"></i> ${__("Include in Template")}
						</button>
						<button type="button" class="btn btn-xs btn-default text-danger btn-action-exclude" ${sel_count === 0 ? "disabled" : ""}>
							<i class="fa fa-ban"></i> ${__("Exclude (Unpublish)")}
						</button>
						<button type="button" class="btn btn-xs btn-default btn-action-create-group" ${sel_count === 0 ? "disabled" : ""}>
							<i class="fa fa-folder-open-o"></i> ${__("Create New Product from Selection")}
						</button>
						<button type="button" class="btn btn-xs btn-default btn-action-add-group" ${sel_count === 0 ? "disabled" : ""}>
							<i class="fa fa-plus"></i> ${__("Add to Existing Product")}
						</button>
						<button type="button" class="btn btn-xs btn-default btn-action-remove-group" ${sel_count === 0 ? "disabled" : ""}>
							<i class="fa fa-times"></i> ${__("Remove from Product")}
						</button>
						<span class="text-muted ml-auto" style="font-size:11px;">
							${__("{0} of {1} selected", [sel_count, total])}
						</span>
					</div>
				`;

				let rows_html = variants.map(v => {
					let is_checked = selected.has(v.item_code);
					let mode = v.mode || "With Template";
					let status_badge = '';
					if (mode === "With Template") {
						status_badge = `<span class="badge" style="background:#28a745; color:white; font-size:11px;">${__("With Template")}</span>`;
					} else if (mode === "Not Published") {
						status_badge = `<span class="badge" style="background:#6c757d; color:white; font-size:11px;">${__("Not Published")}</span>`;
					} else if (mode === "Separate Product") {
						let grp_link = v.group ? `<a href="/desk/woo-variant-group/${v.group}" style="color:white; text-decoration:underline;">${v.group}</a>` : '';
						status_badge = `<span class="badge" style="background:#17a2b8; color:white; font-size:11px;">${__("Separate")}: ${grp_link}</span>`;
					}

					let woo_id_badge = v.woo_product_id
						? `<span class="text-success font-weight-bold">#${v.woo_product_id}</span>`
						: `<span class="text-muted">-</span>`;

					let attr_text = '';
					if (v.attributes && Object.keys(v.attributes).length > 0) {
						attr_text = Object.entries(v.attributes).map(([k, val]) => {
							return `<span class="text-muted">${frappe.utils.escape_html(k)}:</span> <b>${frappe.utils.escape_html(val)}</b>`;
						}).join(" | ");
					}

					let link = v.woo_item_name
						? `<a href="/desk/woo-item/${v.woo_item_name}">${frappe.utils.escape_html(v.sku || v.item_code)}</a>`
						: frappe.utils.escape_html(v.sku || v.item_code);

					return `
						<tr data-item-code="${frappe.utils.escape_html(v.item_code)}">
							<td style="width:30px; text-align:center;">
								<input type="checkbox" class="var-row-cb" data-item-code="${frappe.utils.escape_html(v.item_code)}" ${is_checked ? "checked" : ""}>
							</td>
							<td>${link}</td>
							<td>${frappe.utils.escape_html(v.item_name || "")}</td>
							<td>${attr_text}</td>
							<td>${status_badge}</td>
							<td>${woo_id_badge}</td>
							<td style="text-align:right;"><b>${v.stock_qty}</b></td>
						</tr>
					`;
				}).join('');

				let all_checked = total > 0 && selected.size === total;
				let table_html = `
					<div class="variants-interactive-container" style="margin-top:10px;">
						${summary_html}
						${filter_pills_html}
						${toolbar_html}
						<div class="table-responsive" style="max-height:500px; overflow-y:auto; border:1px solid #e2e8f0; border-radius:4px;">
							<table class="table table-bordered table-hover table-sm text-sm mb-0">
								<thead class="thead-light" style="position:sticky; top:0; z-index:1; background:#f8f9fa;">
									<tr>
										<th style="width:30px; text-align:center;">
											<input type="checkbox" class="var-select-all-cb" ${all_checked ? "checked" : ""}>
										</th>
										<th>${__("Variation / SKU")}</th>
										<th>${__("Item Name")}</th>
										<th>${__("Attributes")}</th>
										<th>${__("Publish Status")}</th>
										<th>${__("Woo ID")}</th>
										<th style="text-align:right;">${__("Stock")}</th>
									</tr>
								</thead>
								<tbody>
									${rows_html}
								</tbody>
							</table>
						</div>
					</div>
				`;

				$wrapper.html(table_html);
				bind_events();
			}

			function bind_events() {
				$wrapper.find(".var-select-all-cb").on("change", function () {
					let checked = $(this).prop("checked");
					if (checked) {
						variants.forEach(v => selected.add(v.item_code));
					} else {
						selected.clear();
					}
					render();
				});

				$wrapper.find(".var-row-cb").on("change", function () {
					let code = $(this).attr("data-item-code");
					if ($(this).prop("checked")) {
						selected.add(code);
					} else {
						selected.delete(code);
					}
					render();
				});

				$wrapper.find(".attr-select-pill").on("click", function () {
					let attr = $(this).attr("data-attr");
					let val = $(this).attr("data-val");
					let matching = variants.filter(v => v.attributes && v.attributes[attr] === val);
					let all_sel = matching.every(v => selected.has(v.item_code));
					if (all_sel) {
						matching.forEach(v => selected.delete(v.item_code));
					} else {
						matching.forEach(v => selected.add(v.item_code));
					}
					render();
				});

				$wrapper.find(".btn-action-include").on("click", function () {
					if (!selected.size) return;
					frappe.call({
						method: "woo_prime.woo_prime.doctype.woo_item.woo_item.set_variants_publish_mode",
						args: {
							template: frm.doc.item_code,
							item_codes: Array.from(selected),
							mode: "With Template"
						},
						freeze: true,
						freeze_message: __("Updating variants..."),
						callback: function (res) {
							if (res && res.message) {
								frappe.show_alert({ message: res.message.message || __("Variants updated"), indicator: "green" });
								render_interactive_variants_table(frm);
							}
						}
					});
				});

				$wrapper.find(".btn-action-exclude").on("click", function () {
					if (!selected.size) return;
					frappe.confirm(
						__("Are you sure you want to exclude {0} variant(s)? If they are published on WooCommerce, their variations will be deleted.", [selected.size]),
						function () {
							frappe.call({
								method: "woo_prime.woo_prime.doctype.woo_item.woo_item.set_variants_publish_mode",
								args: {
									template: frm.doc.item_code,
									item_codes: Array.from(selected),
									mode: "Not Published"
								},
								freeze: true,
								freeze_message: __("Excluding variants..."),
								callback: function (res) {
									if (res && res.message) {
										frappe.show_alert({ message: res.message.message || __("Variants excluded"), indicator: "orange" });
										render_interactive_variants_table(frm);
									}
								}
							});
						}
					);
				});

				$wrapper.find(".btn-action-create-group").on("click", function () {
					if (!selected.size) return;
					let sel_variants = variants.filter(v => selected.has(v.item_code));
					let in_other_group = sel_variants.find(v => v.mode === "Separate Product");
					if (in_other_group) {
						frappe.msgprint({
							title: __("Cannot Create Product"),
							message: __("Variant '{0}' already belongs to group '{1}'. Please remove it from that group first.", [in_other_group.item_code, in_other_group.group]),
							indicator: "red"
						});
						return;
					}

					let first_var = sel_variants[0];
					let first_attr_val = first_var && first_var.attributes ? Object.values(first_var.attributes)[0] : "GRP";
					let clean_attr = (first_attr_val || "GRP").replace(/[^a-zA-Z0-9_-]/g, "");
					let suggested_sku = `${frm.doc.sku || frm.doc.item_code}-${clean_attr}`;

					let leader_options = sel_variants.map(v => {
						let attr_str = v.attributes ? Object.values(v.attributes).join(", ") : "";
						return {
							value: v.item_code,
							label: `${v.item_code} (${v.sku || ""}) - ${attr_str}`
						};
					});

					let d = new frappe.ui.Dialog({
						title: __("Create New Product from Selection"),
						fields: [
							{
								label: __("Product Name"),
								fieldname: "product_name",
								fieldtype: "Data",
								default: `${frm.doc.item_name || frm.doc.item_code} - ${first_attr_val}`
							},
							{
								label: __("Group SKU"),
								fieldname: "group_sku",
								fieldtype: "Data",
								reqd: 1,
								default: suggested_sku
							},
							{
								label: __("Leader Variant"),
								fieldname: "leader",
								fieldtype: "Select",
								reqd: 1,
								options: leader_options,
								default: leader_options[0] ? leader_options[0].value : ""
							}
						],
						primary_action_label: __("Create Product"),
						primary_action: function (values) {
							d.hide();
							frappe.call({
								method: "woo_prime.woo_prime.doctype.woo_item.woo_item.create_variant_group_from_selection",
								args: {
									template: frm.doc.item_code,
									item_codes: Array.from(selected),
									group_sku: values.group_sku,
									item_name: values.product_name,
									leader: values.leader
								},
								freeze: true,
								freeze_message: __("Creating variant group..."),
								callback: function (res) {
									if (res && res.message) {
										frappe.msgprint({
											title: __("Product Group Created"),
											message: __("Woo Variant Group <a href='/desk/woo-variant-group/{0}'><b>{0}</b></a> created successfully with {1} variant(s).", [res.message.group, selected.size]),
											indicator: "green"
										});
										render_interactive_variants_table(frm);
									}
								}
							});
						}
					});
					d.show();
				});

				$wrapper.find(".btn-action-add-group").on("click", function () {
					if (!selected.size) return;
					let sel_variants = variants.filter(v => selected.has(v.item_code));
					let in_other_group = sel_variants.find(v => v.mode === "Separate Product");
					if (in_other_group) {
						frappe.msgprint({
							title: __("Cannot Add to Product"),
							message: __("Variant '{0}' already belongs to group '{1}'. Please remove it from that group first.", [in_other_group.item_code, in_other_group.group]),
							indicator: "red"
						});
						return;
					}

					let d = new frappe.ui.Dialog({
						title: __("Add Selected Variants to Existing Product"),
						fields: [
							{
								label: __("Select Woo Variant Group"),
								fieldname: "group",
								fieldtype: "Link",
								options: "Woo Variant Group",
								reqd: 1,
								get_query: function () {
									return {
										filters: {
											template_item: frm.doc.item_code
										}
									};
								}
							}
						],
						primary_action_label: __("Add to Product"),
						primary_action: function (values) {
							d.hide();
							frappe.call({
								method: "woo_prime.woo_prime.doctype.woo_item.woo_item.add_to_variant_group",
								args: {
									group: values.group,
									item_codes: Array.from(selected)
								},
								freeze: true,
								freeze_message: __("Adding to group..."),
								callback: function (res) {
									if (res && res.message) {
										frappe.show_alert({ message: res.message.message || __("Variants added to group"), indicator: "green" });
										render_interactive_variants_table(frm);
									}
								}
							});
						}
					});
					d.show();
				});

				$wrapper.find(".btn-action-remove-group").on("click", function () {
					if (!selected.size) return;
					let group_members = variants.filter(v => selected.has(v.item_code) && v.mode === "Separate Product");
					if (!group_members.length) {
						frappe.msgprint({
							title: __("No Group Members"),
							message: __("None of the selected variants currently belong to a separate product group."),
							indicator: "orange"
						});
						return;
					}

					frappe.confirm(
						__("Remove {0} variant(s) from their group? They will be set to 'Not Published' and deleted from WooCommerce.", [group_members.length]),
						function () {
							frappe.call({
								method: "woo_prime.woo_prime.doctype.woo_item.woo_item.remove_from_variant_group",
								args: {
									item_codes: group_members.map(v => v.item_code)
								},
								freeze: true,
								freeze_message: __("Removing from group..."),
								callback: function (res) {
									if (res && res.message) {
										frappe.show_alert({ message: res.message.message || __("Variants removed from group"), indicator: "green" });
										render_interactive_variants_table(frm);
									}
								}
							});
						}
					);
				});
			}

			render();
		}
	});
}


