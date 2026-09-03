// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Settings", {
	refresh(frm) {
		// Populate Webhook Delivery URL
		const webhook_url = frappe.urllib.get_base_url() + "/api/method/woo_prime.api.webhook.handle_order";
		if (frm.doc.webhook_delivery_url !== webhook_url) {
			frm.set_value("webhook_delivery_url", webhook_url);
		}

		// Test Connection button styling
		if (frm.fields_dict.test_connection_btn) {
			frm.fields_dict.test_connection_btn.$input.addClass("btn-primary");
		}

		// Fetch Categories button styling
		if (frm.fields_dict.fetch_categories_btn) {
			frm.fields_dict.fetch_categories_btn.$input.addClass("btn-info");
		}

		// Download Plugin button styling
		if (frm.fields_dict.download_plugin_btn) {
			frm.fields_dict.download_plugin_btn.$input.addClass("btn-success");
		}

		// Add custom button to copy Webhook Delivery URL
		frm.add_custom_button(
			__("Copy Webhook URL"),
			function () {
				const url = frappe.urllib.get_base_url() + "/api/method/woo_prime.api.webhook.handle_order";
				navigator.clipboard.writeText(url).then(() => {
					frappe.show_alert({ message: __("Webhook Delivery URL copied to clipboard!"), indicator: "green" });
				});
			}
		);

		// Add custom button for plugin download
		frm.add_custom_button(
			__("Download WordPress Plugin"),
			function () {
				window.open(
					"/api/method/woo_prime.woo_prime.doctype.woo_settings.woo_settings.download_wordpress_plugin"
				);
			}
		);

		// Add custom button for category fetch
		frm.add_custom_button(
			__("Fetch WooCommerce Categories"),
			function () {
				frappe.call({
					method: "woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo",
					freeze: true,
					freeze_message: __("Fetching product categories from WooCommerce..."),
					callback: function (r) {
						if (r && r.message) {
							frappe.show_alert({
								message: __("Synced {0} categories!", [r.message]),
								indicator: "green",
							});
						}
					},
				});
			},
			__("Sync")
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

	fetch_categories_btn(frm) {
		if (!frm.doc.woo_site_url || !frm.doc.consumer_key) {
			frappe.msgprint(__("Please set up WooCommerce connection first."));
			return;
		}

		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo",
			freeze: true,
			freeze_message: __("Fetching product categories from WooCommerce..."),
			callback: function (r) {
				if (r && r.message) {
					frappe.show_alert({
						message: __("Synced {0} categories!", [r.message]),
						indicator: "green",
					});
				}
			},
		});
	},

	download_plugin_btn(frm) {
		window.open(
			"/api/method/woo_prime.woo_prime.doctype.woo_settings.woo_settings.download_wordpress_plugin"
		);
	},
});

