// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Attribute", {
	refresh(frm) {
		if (frm.doc.woo_attribute_id) {
			frm.dashboard.set_headline(
				__('<span class="indicator-pill green">WooCommerce Global Attribute ID: {0}</span>', [frm.doc.woo_attribute_id])
			);
		} else {
			frm.dashboard.set_headline(
				__('<span class="indicator-pill orange">Not Pushed to WooCommerce</span>')
			);
		}

		if (!frm.is_new()) {
			// Sync to WooCommerce button
			frm.add_custom_button(
				frm.doc.woo_attribute_id ? __("Sync to WooCommerce") : __("Push to WooCommerce"),
				function () {
					let do_sync = function () {
						frappe.call({
							method: "sync_to_woocommerce",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Syncing Attribute & Swatches to WooCommerce..."),
							callback: function (r) {
								if (r && r.message && r.message.status === "success") {
									frappe.show_alert({
										message: r.message.message,
										indicator: "green",
									}, 5);
									frm.reload_doc();
								}
							},
						});
					};

					if (frm.is_dirty()) {
						frm.save().then(do_sync);
					} else {
						do_sync();
					}
				},
				__("Actions")
			);

			// Populate term swatches from ERPNext button
			frm.add_custom_button(
				__("Reload Swatch Rows"),
				function () {
					frm.call({
						method: "reload_swatch_rows",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Loading terms..."),
						callback: function (r) {
							frm.reload_doc();
						},
					});
				},
				__("Actions")
			);
		}

		// Load and render terms preview table
		frm.trigger("load_terms_preview");

		// Show/hide swatch section label hint
		frm.trigger("type");
	},

	type(frm) {
		let type = frm.doc.type || "select";
		let swatch_section = frm.get_field("swatch_section");
		if (swatch_section) {
			let hint = "";
			if (type === "color") {
				hint = __("🎨 Color Swatch — set a Hex color per term below.");
			} else if (type === "image") {
				hint = __("🖼 Image Swatch — upload a thumbnail image per term below.");
			} else if (type === "button" || type === "radio") {
				hint = __("🔘 Text / Button Swatch — optionally set a custom label per term below.");
			} else {
				hint = __("ℹ Swatch Type is 'select' (default dropdown). Change Type above to enable swatches.");
			}
			// Show hint in swatch section description area
			if (frm.fields_dict["swatch_section"] && frm.fields_dict["swatch_section"].$wrapper) {
				let $desc = frm.fields_dict["swatch_section"].$wrapper.find(".section-description");
				if (!$desc.length) {
					frm.fields_dict["swatch_section"].$wrapper.find(".section-head").after(
						`<div class="section-description text-muted small" style="padding:4px 15px 6px;">${hint}</div>`
					);
				} else {
					$desc.html(hint);
				}
			}
		}
	},

	erpnext_item_attribute(frm) {
		frm.trigger("load_terms_preview");
		// When attribute changes, auto-save so swatch rows get populated
		if (!frm.is_new() && frm.doc.erpnext_item_attribute) {
			frappe.show_alert({
				message: __("Save the form to auto-populate swatch rows for this attribute's terms."),
				indicator: "blue",
			}, 4);
		}
	},

	load_terms_preview(frm) {
		if (!frm.doc.name || !frm.fields_dict["terms_html"]) return;

		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_attribute.woo_attribute.get_attribute_terms_info",
			args: { woo_attribute_name: frm.doc.name },
			callback: function (r) {
				if (r && r.message && r.message.terms && r.message.terms.length) {
					let rows = r.message.terms.map((t, idx) => `
						<tr>
							<td class="text-muted" style="width:40px;">${idx + 1}</td>
							<td><b>${frappe.utils.escape_html(t.attribute_value)}</b></td>
							<td><code>${frappe.utils.escape_html(t.abbr || "-")}</code></td>
						</tr>
					`).join("");

					let html = `
						<div style="margin-top:10px;">
							<div class="text-muted small mb-2">
								${__("These terms from the linked ERPNext Item Attribute will be pushed to WooCommerce:")}
							</div>
							<table class="table table-bordered table-sm text-sm">
								<thead>
									<tr class="bg-light text-muted">
										<th>#</th>
										<th>${__("Term / Value Name")}</th>
										<th>${__("Abbreviation")}</th>
									</tr>
								</thead>
								<tbody>${rows}</tbody>
							</table>
						</div>
					`;
					frm.fields_dict["terms_html"].$wrapper.html(html);
				} else if (frm.fields_dict["terms_html"].$wrapper) {
					frm.fields_dict["terms_html"].$wrapper.html(
						`<div class="text-muted text-sm" style="padding:10px 0;">
							${__("No terms found. Please select a Linked ERPNext Item Attribute that has values.")}
						</div>`
					);
				}
			},
		});
	},
});

// Grid / Child table events for Woo Attribute Term
frappe.ui.form.on("Woo Attribute Term", {
	swatch_type(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		// Clear irrelevant fields when type changes
		if (row.swatch_type !== "color") frappe.model.set_value(cdt, cdn, "swatch_color", "");
		if (row.swatch_type !== "image") frappe.model.set_value(cdt, cdn, "swatch_image", "");
		if (row.swatch_type !== "text") frappe.model.set_value(cdt, cdn, "swatch_text", "");
	},
});
