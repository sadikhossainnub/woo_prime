// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item Attribute", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Push to WooCommerce"),
				function () {
					frappe.confirm(
						__(
							"Push attribute <b>{0}</b> and its values to WooCommerce as a Global Attribute?<br><br>Existing attribute and terms on WooCommerce will be linked or updated.",
							[frm.doc.attribute_name || frm.doc.name]
						),
						function () {
							frappe.call({
								method: "woo_prime.woo_prime.doctype.woo_attribute.woo_attribute.push_attribute_to_woo",
								args: {
									attribute_name: frm.doc.name,
								},
								freeze: true,
								freeze_message: __("Pushing attribute and terms to WooCommerce..."),
								callback: function (r) {
									if (r.message && r.message.status === "success") {
										frappe.msgprint({
											title: __("Pushed to WooCommerce"),
											message: r.message.message || __("Attribute pushed successfully!"),
											indicator: "green",
										});
									}
									frm.reload_doc();
								},
							});
						}
					);
				},
				__("WooCommerce")
			);
		}
	},
});

