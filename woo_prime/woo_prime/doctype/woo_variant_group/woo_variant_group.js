// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Variant Group", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Publish Group"),
				function () {
					frappe.call({
						method: "publish_group",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Publishing group to WooCommerce..."),
						callback: function (r) {
							frm.reload_doc();
						}
					});
				},
				__("Actions")
			);

			if (frm.doc.woo_product_id) {
				frm.add_custom_button(
					__("Unpublish Group"),
					function () {
						frappe.confirm(
							__("Are you sure you want to unpublish this group from WooCommerce? This will delete the variable product and its variations from WooCommerce, and set all members to 'Not Published'."),
							function () {
								frappe.call({
									method: "unpublish_group",
									doc: frm.doc,
									freeze: true,
									freeze_message: __("Unpublishing group..."),
									callback: function (r) {
										frm.reload_doc();
									}
								});
							}
						);
					},
					__("Actions")
				);
			}

			if (frm.doc.woo_product_url) {
				frm.add_custom_button(
					__("View on WooCommerce"),
					function () {
						window.open(frm.doc.woo_product_url, "_blank");
					},
					__("Actions")
				);
			}

			// Render members HTML
			if (frm.fields_dict["members_html"]) {
				frappe.call({
					method: "get_group_members_info",
					doc: frm.doc,
					callback: function (r) {
						if (r && r.message && r.message.length) {
							let rows = r.message.map(function (m) {
								let leaderBadge = m.is_leader
									? `<span class="badge badge-primary" style="margin-left:5px;">Leader</span>`
									: "";
								let wooBadge = m.published
									? `<span class="indicator-pill green">#${m.woo_product_id || ""}</span>`
									: `<span class="indicator-pill gray">${__("Not Published")}</span>`;
								let attrStr = Object.entries(m.attributes || {})
									.map(([k, v]) => `${frappe.utils.escape_html(k)}: <b>${frappe.utils.escape_html(v)}</b>`)
									.join(" | ");

								return `<tr>
									<td><a href="/desk/woo-item/${encodeURIComponent(m.woo_item_name)}">${frappe.utils.escape_html(m.sku || m.item_code)}</a> ${leaderBadge}</td>
									<td>${frappe.utils.escape_html(m.item_name || "")}</td>
									<td>${attrStr || "-"}</td>
									<td>${wooBadge}</td>
									<td><b>${m.stock_qty}</b></td>
								</tr>`;
							}).join("");

							let html = `
								<div class="table-responsive" style="margin-top:10px;">
									<table class="table table-bordered table-sm text-sm">
										<thead>
											<tr class="text-muted bg-light">
												<th>${__("Variation / SKU")}</th>
												<th>${__("Item Name")}</th>
												<th>${__("Attributes")}</th>
												<th>${__("Woo ID")}</th>
												<th>${__("Stock Qty")}</th>
											</tr>
										</thead>
										<tbody>${rows}</tbody>
									</table>
								</div>
							`;
							frm.fields_dict["members_html"].$wrapper.html(html);
						} else {
							frm.fields_dict["members_html"].$wrapper.html(
								`<div class="text-muted text-sm" style="padding:10px 0;">${__("No member variants currently attached to this group.")}</div>`
							);
						}
					}
				});
			}
		}
	}
});
