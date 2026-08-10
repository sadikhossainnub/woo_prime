// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Settings", {
	refresh(frm) {
		// Test Connection button styling
		if (frm.fields_dict.test_connection_btn) {
			frm.fields_dict.test_connection_btn.$input.addClass("btn-primary");
		}

		// Download Plugin button styling
		if (frm.fields_dict.download_plugin_btn) {
			frm.fields_dict.download_plugin_btn.$input.addClass("btn-success");
		}

		// Add custom button in header
		frm.add_custom_button(
			__("Download WordPress Plugin"),
			function () {
				window.open(
					"/api/method/woo_prime.woo_prime.doctype.woo_settings.woo_settings.download_wordpress_plugin"
				);
			},
			__("WordPress Plugin")
		);
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

	download_plugin_btn(frm) {
		window.open(
			"/api/method/woo_prime.woo_prime.doctype.woo_settings.woo_settings.download_wordpress_plugin"
		);
	},
});
