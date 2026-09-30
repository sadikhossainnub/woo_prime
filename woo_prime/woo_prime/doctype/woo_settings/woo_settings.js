// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Settings", {
	refresh(frm) {
		// Populate Webhook Delivery URL
		const webhook_url = frappe.urllib.get_base_url() + "/api/method/woo_prime.api.webhook.handle_order";
		if (frm.doc.webhook_delivery_url !== webhook_url) {
			frm.set_value("webhook_delivery_url", webhook_url);
		}

		// Button styling
		if (frm.fields_dict.test_connection_btn) {
			frm.fields_dict.test_connection_btn.$input.addClass("btn-primary");
		}
		if (frm.fields_dict.fetch_categories_btn) {
			frm.fields_dict.fetch_categories_btn.$input.addClass("btn-info");
		}
		if (frm.fields_dict.fetch_items_btn) {
			frm.fields_dict.fetch_items_btn.$input.addClass("btn-warning");
		}
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

		// Add custom button for items fetch
		frm.add_custom_button(
			__("Fetch WooCommerce Items"),
			function () {
				frappe.call({
					method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
					args: { auto_create_missing: true, batch_size: 10, background: true },
					callback: function (r) {
						if (r && r.message) {
							frappe.show_alert({
								message: r.message.message || __("Started background product fetch (10 items per batch)..."),
								indicator: "blue",
							});
						}
					},
				});
			},
			__("Sync")
		);

		// Add custom button for orders fetch
		frm.add_custom_button(
			__("Fetch WooCommerce Orders"),
			function () {
				frappe.call({
					method: "woo_prime.api.sync.auto_sync_orders",
					args: { force: true },
					freeze: true,
					freeze_message: __("Fetching orders from WooCommerce & creating Sales Orders..."),
					callback: function (r) {
						frm.reload_doc();
					},
				});
			},
			__("Sync")
		);

		// Add Run Full Sync button
		frm.add_custom_button(
			__("Run Full Sync"),
			function () {
				frappe.confirm(
					__("Run Full End-to-End Sync? This will sync Categories, Products, SKU Links, Stock, and Prices."),
					function () {
						frappe.call({
							method: "run_full_sync",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Running Full WooCommerce Sync... Please wait..."),
							callback: function (r) {
								frm.reload_doc();
							},
						});
					}
				);
			},
			__("Sync")
		);

		// Add Fetch Missing Order button
		frm.add_custom_button(
			__("Fetch WooCommerce Order by ID"),
			function () {
				frappe.prompt(
					[
						{
							fieldtype: "Data",
							fieldname: "woo_order_id",
							label: __("WooCommerce Order ID (e.g. 2045)"),
							reqd: 1,
						},
					],
					function (values) {
						frappe.call({
							method: "fetch_missing_order",
							doc: frm.doc,
							args: { woo_order_id: values.woo_order_id },
							freeze: true,
							freeze_message: __("Fetching order from WooCommerce..."),
							callback: function (r) {
								if (r.message) {
									frappe.set_route("Form", "Sales Order", r.message);
								}
							},
						});
					},
					__("Fetch Order manually"),
					__("Fetch & Sync Order")
				);
			},
			__("Sync")
		);
	},

	generate_secret_btn(frm) {
		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_settings.woo_settings.generate_api_shared_secret",
			callback: function (r) {
				if (r && r.message) {
					frm.set_value("api_shared_secret", r.message);
					frappe.show_alert({ message: __("Generated new API Shared Secret! Remember to save Woo Settings."), indicator: "green" });
				}
			}
		});
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

	fetch_items_btn(frm) {
		if (!frm.doc.woo_site_url || !frm.doc.consumer_key) {
			frappe.msgprint(__("Please set up WooCommerce connection first."));
			return;
		}

		frappe.call({
			method: "woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
			args: { auto_create_missing: true, batch_size: 10, background: true },
			callback: function (r) {
				if (r && r.message) {
					frappe.show_alert({
						message: r.message.message || __("Started background product fetch (10 items per batch)..."),
						indicator: "blue",
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
