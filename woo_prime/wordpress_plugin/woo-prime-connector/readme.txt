=== Woo Prime Connector ===
Contributors: Prime Tech BD
Tags: woocommerce, erpnext, pricing rules, loyalty points, integration, sync, dashboard
Requires at least: 5.8
Tested up to: 6.4
Requires PHP: 7.4
Stable tag: 2.1.0
License: GPLv2 or later

Connects WooCommerce to ERPNext for real-time Pricing Rules evaluation, Loyalty Points balance & redemption, and sync dashboard.

== Description ==
Woo Prime Connector is a powerful, lightweight WordPress plugin that connects your WooCommerce store to ERPNext in real-time.

Features in v2.1:
1. Real-time Pricing Rules: Evaluates ERPNext Pricing Rules dynamically in WooCommerce Cart with per-item strikethrough price breakdown.
2. Loyalty Points Balance & Redemption: Displays ERPNext Customer Loyalty Points balance on Checkout page with instant AJAX redemption toggle.
3. Shared Secret Token Security: Secure X-Woo-Prime-Token authentication for ERPNext endpoints.
4. WP Admin Dashboard Widget: Live connection badge and sync statistics on your WordPress Dashboard.
5. Connection Test Tool: Test your ERPNext REST API connection directly from WooCommerce settings with instant feedback.
6. Transient Caching & Debug Logging: Fast response times with built-in caching and error logging for troubleshooting.

== Installation ==
1. Upload the `woo-prime-connector` folder to `/wp-content/plugins/` directory.
2. Activate the plugin through the 'Plugins' menu in WordPress.
3. Go to WooCommerce -> Woo Prime ERPNext and enter your ERPNext Site URL and Shared Secret Token.

== Changelog ==
= 2.1.0 =
* Added Shared Secret Token (`X-Woo-Prime-Token`) header support for secure communication with ERPNext endpoints.
* Fixed Cloudflare User-Agent compatibility (`curl/8.5.0` default / overridable).
* Improved error handling and diagnostic reporting during API connection test.

= 2.0.0 =
* Initial major release with real-time pricing rule calculation and loyalty points redemption.

