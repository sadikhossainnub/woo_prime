// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Settings", {
	refresh(frm) {
		// Test Connection button
		frm.fields_dict.test_connection_btn.$input.addClass("btn-primary");
	},

	test_connection_btn(frm) {
		if (!frm.doc.woo_site_url || !frm.doc.consumer_key) {
			frappe.msgprint(__("Please fill in WooCommerce Site URL and Consumer Key first."));
			return;
		}

		frappe.call({
			method: "test_connection",
			doc: frm.doc,
			freeze: true,
			freeze_message: __("Testing WooCommerce Connection..."),
		});
	},
});
