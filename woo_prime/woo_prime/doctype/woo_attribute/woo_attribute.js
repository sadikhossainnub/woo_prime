// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Attribute", {
	refresh(frm) {
		if (frm.doc.woo_attribute_id) {
			frm.dashboard.set_headline(
				__('<span class="indicator-pill green">WooCommerce Global Attribute ID: {0}</span>', [frm.doc.woo_attribute_id])
			);
		}
	},
});

