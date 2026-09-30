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
				frappe.confirm(
					__('Fetch all product categories from WooCommerce? This will run in the background.'),
					function() {
						// Show progress dialog
						show_fetch_progress_dialog('fetch_categories');
						
						// Start background fetch
						frappe.call({
							method: 'woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo',
							args: {
								background: true
							},
							callback: function(r) {
								if (r.message && r.message.status === 'queued') {
									frappe.show_alert({
										message: __('Category fetch queued successfully'),
										indicator: 'blue'
									}, 5);
								}
							}
						});
					}
				);
			},
			__("Sync")
		);

		// Add custom button for items fetch
		frm.add_custom_button(
			__("Fetch WooCommerce Items"),
			function () {
				// Create dialog with fetch options
				let d = new frappe.ui.Dialog({
					title: __('Fetch Items from WooCommerce'),
					fields: [
						{
							fieldname: 'batch_size',
							fieldtype: 'Int',
							label: __('Products per Page'),
							default: 10,
							reqd: 1,
							description: __('Number of products to fetch per API request (1-100)')
						},
						{
							fieldname: 'auto_create_missing',
							fieldtype: 'Check',
							label: __('Auto-create ERPNext Items'),
							default: 0,  // Changed to 0 (False)
							description: __('Automatically create ERPNext Item records for unmatched products')
						},
						{
							fieldname: 'skip_empty_products',
							fieldtype: 'Check',
							label: __('Skip Empty Products'),
							default: frm.doc.skip_empty_products || 0,
							description: __('Skip products with empty name or SKU')
						}
					],
					primary_action_label: __('Start Fetch'),
					primary_action: function(values) {
						d.hide();
						
						// Show progress dialog
						show_fetch_progress_dialog('fetch_products');
						
						// Start background fetch
						frappe.call({
							method: 'woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce',
							args: {
								batch_size: values.batch_size,
								auto_create_missing: values.auto_create_missing,
								skip_empty_products: values.skip_empty_products,
								background: true
							},
							callback: function(r) {
								if (r.message && r.message.status === 'queued') {
									frappe.show_alert({
										message: __('Product fetch queued successfully'),
										indicator: 'blue'
									}, 5);
								}
							}
						});
					}
				});
				
				d.show();
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

		frappe.confirm(
			__('Fetch all product categories from WooCommerce? This will run in the background.'),
			function() {
				// Show progress dialog
				show_fetch_progress_dialog('fetch_categories');
				
				// Start background fetch
				frappe.call({
					method: 'woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo',
					args: {
						background: true
					},
					callback: function(r) {
						if (r.message && r.message.status === 'queued') {
							frappe.show_alert({
								message: __('Category fetch queued successfully'),
								indicator: 'blue'
							}, 5);
						}
					}
				});
			}
		);
	},

	fetch_items_btn(frm) {
		if (!frm.doc.woo_site_url || !frm.doc.consumer_key) {
			frappe.msgprint(__("Please set up WooCommerce connection first."));
			return;
		}

		// Create dialog with fetch options
		let d = new frappe.ui.Dialog({
			title: __('Fetch Items from WooCommerce'),
			fields: [
				{
					fieldname: 'batch_size',
					fieldtype: 'Int',
					label: __('Products per Page'),
					default: 10,
					reqd: 1,
					description: __('Number of products to fetch per API request (1-100)')
				},
				{
					fieldname: 'auto_create_missing',
					fieldtype: 'Check',
					label: __('Auto-create ERPNext Items'),
					default: 0,  // Changed to 0 (False)
					description: __('Automatically create ERPNext Item records for unmatched products')
				},
				{
					fieldname: 'skip_empty_products',
					fieldtype: 'Check',
					label: __('Skip Empty Products'),
					default: frm.doc.skip_empty_products || 0,
					description: __('Skip products with empty name or SKU')
				}
			],
			primary_action_label: __('Start Fetch'),
			primary_action: function(values) {
				d.hide();
				
				// Show progress dialog
				show_fetch_progress_dialog('fetch_products');
				
				// Start background fetch
				frappe.call({
					method: 'woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce',
					args: {
						batch_size: values.batch_size,
						auto_create_missing: values.auto_create_missing,
						skip_empty_products: values.skip_empty_products,
						background: true
					},
					callback: function(r) {
						if (r.message && r.message.status === 'queued') {
							frappe.show_alert({
								message: __('Product fetch queued successfully'),
								indicator: 'blue'
							}, 5);
						}
					}
				});
			}
		});
		
		d.show();
	},

	download_plugin_btn(frm) {
		window.open(
			"/api/method/woo_prime.woo_prime.doctype.woo_settings.woo_settings.download_wordpress_plugin"
		);
	},
});

// Progress dialog
let progress_dialog = null;
let progress_subscription = null;

function show_fetch_progress_dialog(operation) {
	// Close existing dialog if any
	if (progress_dialog) {
		progress_dialog.hide();
	}
	
	// Create progress dialog
	progress_dialog = new frappe.ui.Dialog({
		title: operation === 'fetch_products' ? __('Fetching Products') : __('Fetching Categories'),
		indicator: 'blue',
		size: 'large',
		fields: [
			{
				fieldname: 'progress_html',
				fieldtype: 'HTML'
			}
		],
		primary_action_label: __('Close'),
		primary_action: function() {
			progress_dialog.hide();
			if (progress_subscription) {
				frappe.realtime.off('woo_fetch_progress', progress_subscription);
				progress_subscription = null;
			}
		}
	});
	
	// Initialize progress HTML
	let html = `
		<div class="woo-fetch-progress">
			<div class="progress" style="height: 30px; margin-bottom: 20px;">
				<div class="progress-bar progress-bar-striped progress-bar-animated" 
					role="progressbar" 
					style="width: 0%;" 
					id="woo-progress-bar">
				</div>
			</div>
			<div class="row" style="margin-bottom: 15px;">
				<div class="col-sm-12">
					<p id="woo-status-message" style="font-size: 14px; color: #555;">
						<i class="fa fa-spinner fa-spin"></i> Initializing...
					</p>
				</div>
			</div>
			<div class="row" id="woo-stats-row" style="display: none;">
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-fetched">0</h4>
						<p class="text-muted" style="margin: 0;">Fetched</p>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-linked">0</h4>
						<p class="text-muted" style="margin: 0;">Linked</p>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-created">0</h4>
						<p class="text-muted" style="margin: 0;">Created</p>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-failed">0</h4>
						<p class="text-muted" style="margin: 0;">Failed</p>
					</div>
				</div>
			</div>
		</div>
	`;
	
	progress_dialog.fields_dict.progress_html.$wrapper.html(html);
	progress_dialog.show();
	
	// Subscribe to realtime updates
	progress_subscription = function(data) {
		if (data.operation !== operation) {
			return;
		}
		
		update_progress_dialog(data);
		
		// Auto-close on completion or error after 5 seconds
		if (data.status === 'completed' || data.status === 'error') {
			setTimeout(function() {
				if (progress_dialog) {
					progress_dialog.hide();
				}
			}, 5000);
		}
	};
	
	frappe.realtime.on('woo_fetch_progress', progress_subscription);
}

function update_progress_dialog(data) {
	if (!progress_dialog) {
		return;
	}
	
	// Update status message
	let status_icon = 'fa-spinner fa-spin';
	let status_color = '#555';
	
	if (data.status === 'completed') {
		status_icon = 'fa-check-circle';
		status_color = '#28a745';
	} else if (data.status === 'error') {
		status_icon = 'fa-exclamation-circle';
		status_color = '#dc3545';
	}
	
	$('#woo-status-message').html(
		`<i class="fa ${status_icon}" style="color: ${status_color};"></i> ${data.message}`
	);
	
	// Update progress bar (estimate based on page number if not provided)
	if (data.status === 'completed') {
		$('#woo-progress-bar').css('width', '100%').removeClass('progress-bar-animated');
	} else if (data.status === 'error') {
		$('#woo-progress-bar').removeClass('progress-bar-striped progress-bar-animated').addClass('bg-danger');
	}
	
	// Update stats
	if (data.operation === 'fetch_products') {
		$('#woo-stats-row').show();
		$('#woo-stat-fetched').text(data.total_fetched || 0);
		$('#woo-stat-linked').text(data.linked || 0);
		$('#woo-stat-created').text(data.created || 0);
		$('#woo-stat-failed').text(data.failed || 0);
	} else if (data.operation === 'fetch_categories') {
		// Simplified stats for categories
		$('#woo-stats-row').hide();
	}
}
