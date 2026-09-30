# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import os

import frappe
import requests
from frappe import _
from frappe.model.document import Document


class WooSettings(Document):
	def onload(self):
		self.webhook_delivery_url = frappe.utils.get_url("/api/method/woo_prime.api.webhook.handle_order")

	def validate(self):
		if self.woo_site_url:
			self.woo_site_url = self.woo_site_url.strip().rstrip("/")
			if self.woo_site_url.endswith("/index.php"):
				self.woo_site_url = self.woo_site_url[:-10].rstrip("/")

		if self.consumer_key:
			self.consumer_key = self.consumer_key.strip()

		if self.order_email_notification and not self.notification_email:
			frappe.throw(_("Notification Email Address is required when Email Notification is enabled."))


	@frappe.whitelist()
	@frappe.whitelist()
	def test_connection(self):
		"""Test WooCommerce API connection."""
		import json
		from urllib.parse import urlparse
		try:
			api = get_woo_api()
			host = urlparse(self.woo_site_url or "").netloc or "your-site.com"
			ua_used = getattr(api, "user_agent", "curl/8.5.0")

			# Attempt 1: system_status endpoint
			response = api.get("system_status")
			if response.status_code == 200:
				try:
					data = response.json()
					if isinstance(data, dict):
						environment = data.get("environment", {})
						wc_version = environment.get("version", "Unknown")
						wp_version = environment.get("wp_version", "Unknown")
						frappe.msgprint(
							f"✅ Connection Successful!<br>"
							f"WooCommerce Version: <b>{wc_version}</b><br>"
							f"WordPress Version: <b>{wp_version}</b>",
							title="Connection Test",
							indicator="green",
						)
						return
				except (ValueError, json.JSONDecodeError):
					pass

			# Attempt 2: Fallback test with products endpoint
			prod_response = api.get("products", params={"per_page": 1})
			if prod_response.status_code == 200:
				try:
					prod_data = prod_response.json()
					if isinstance(prod_data, list):
						frappe.msgprint(
							"✅ Connection Successful!<br>"
							"Successfully connected to WooCommerce REST API (Products endpoint).",
							title="Connection Test",
							indicator="green",
						)
						return
				except (ValueError, json.JSONDecodeError):
					pass

			active_resp = prod_response if ('prod_response' in locals() and prod_response is not None) else response
			resp_text = (active_resp.text or "").strip()
			server_hdr = active_resp.headers.get("Server") or active_resp.headers.get("server") or "N/A"
			cf_ray = active_resp.headers.get("cf-ray") or active_resp.headers.get("CF-RAY") or "N/A"
			is_cf = "cloudflare" in server_hdr.lower() or "attention required" in resp_text.lower() or "sorry, you have been blocked" in resp_text.lower() or active_resp.status_code == 403

			# If HTTP 200 returned HTML or non-JSON body
			if active_resp.status_code == 200:
				escaped_preview = frappe.utils.escape_html(resp_text[:300]) if resp_text else "Empty response body"
				msg = (
					"❌ Connection Failed!<br>"
					"The WooCommerce site returned <b>HTTP 200 OK</b>, but the response was not valid JSON.<br><br>"
					"<b>Troubleshooting Non-JSON / HTML Response:</b><br>"
					"1. <b>WordPress Permalinks:</b> Go to WP Admin → Settings → Permalinks and change structure from <i>'Plain'</i> to <i>'Post name'</i>.<br>"
					"2. <b>WooCommerce Plugin:</b> Verify WooCommerce plugin is installed and activated.<br>"
					"3. <b>Site URL:</b> Ensure <i>Woo Site URL</i> is correct and does not redirect to a login page.<br>"
					"4. <b>Security/Cache Plugins:</b> Disable security or caching rules returning HTML pages instead of REST API JSON.<br><br>"
					f"<b>Response Preview:</b> <code>{escaped_preview}</code>"
				)
				frappe.msgprint(msg, title="Connection Test", indicator="red")
				return

			msg = f"❌ Connection Failed!<br>Status Code: {active_resp.status_code}<br>"
			if is_cf:
				msg += (
					f"<br><b>Cloudflare Diagnostic Info:</b><br>"
					f"• <b>CF-Ray ID:</b> <code>{frappe.utils.escape_html(cf_ray)}</code><br>"
					f"• <b>Server Header:</b> <code>{frappe.utils.escape_html(server_hdr)}</code><br>"
					f"• <b>User-Agent Used:</b> <code>{frappe.utils.escape_html(ua_used)}</code><br><br>"
					"<b>Troubleshooting Cloudflare Block:</b><br>"
					f"1. <b>Search Ray ID in Cloudflare:</b> Go to Cloudflare → Security → Events and search Ray ID <code>{frappe.utils.escape_html(cf_ray)}</code> to see which rule blocked the request.<br>"
					"2. <b>Add Scoped Skip Rule:</b> In Cloudflare Dashboard → Security → WAF → Custom Rules, add a Skip rule scoped to your server IP:<br>"
					f"   <code>(http.host eq \"{frappe.utils.escape_html(host)}\" and starts_with(http.request.uri.path, \"/wp-json/\") and ip.src in {{&lt;server IPv4&gt; &lt;server IPv6 /64&gt;}})</code><br>"
					"   <i>Action:</i> <b>Skip</b> (WAF components, Bot Fight Mode, Browser Integrity Check).<br>"
					"   <i>Note:</i> The ERPNext server may connect over IPv6, so include both IPv4 and IPv6 /64 in the IP list.<br>"
					"3. <b>API Key Permissions:</b> Verify REST API Key has <b>Read/Write</b> permissions in WP Admin → WooCommerce → Settings → Advanced → REST API.<br><br>"
				)
			elif active_resp.status_code == 404:
				msg += (
					"<br><b>Troubleshooting 404 Not Found:</b><br>"
					"1. <b>WordPress Permalinks:</b> Go to WP Admin → Settings → Permalinks and change structure from <i>'Plain'</i> to <i>'Post name'</i>.<br>"
					"2. <b>WooCommerce Plugin:</b> Verify WooCommerce is installed and active.<br>"
					"3. <b>Site URL:</b> Ensure <i>Woo Site URL</i> is entered correctly.<br>"
					"4. <b>Apache Config:</b> Ensure <code>mod_rewrite</code> is enabled and <code>AllowOverride All</code> is set in Apache.<br><br>"
				)
			elif active_resp.status_code in (401, 403):
				msg += (
					"<br><b>Troubleshooting Auth Error (401 / 403):</b><br>"
					"1. <b>REST API Key Permissions:</b> Go to WP Admin → WooCommerce → Settings → Advanced → REST API. Edit your API Key and ensure permissions are set to <b>Read/Write</b>.<br>"
					"2. <b>User Account Permissions:</b> Ensure the WordPress user associated with the REST API Key has <b>Administrator</b> or <b>Shop Manager</b> role.<br>"
					"3. <b>Check Credentials:</b> Verify there are no trailing/leading spaces in Consumer Key or Consumer Secret.<br>"
					"4. <b>Apache Authorization Header:</b> If your web server strips HTTP Authorization headers, add the following to your WordPress <code>.htaccess</code> file:<br>"
					"<code>SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1</code> or <code>CGIPassAuth On</code><br><br>"
				)
			escaped_preview = frappe.utils.escape_html(resp_text[:500]) if resp_text else "Empty body"
			msg += f"Response Preview: <code>{escaped_preview}</code>"
			frappe.msgprint(
				msg,
				title="Connection Test",
				indicator="red",
			)
		except Exception as e:
			frappe.log_error(title="woo_prime: test_connection error", message=frappe.get_traceback())
			frappe.msgprint(
				f"❌ Connection Failed!<br>Error: {frappe.utils.escape_html(str(e))}",
				title="Connection Test",
				indicator="red",
			)

	@frappe.whitelist()
	def run_full_sync(self):
		"""Run full end-to-end sync."""
		return run_full_sync()

	@frappe.whitelist()
	def fetch_missing_order(self, woo_order_id=None):
		"""Fetch a specific order from WooCommerce by ID."""
		return fetch_missing_order(woo_order_id)


@frappe.whitelist()
def generate_api_shared_secret():
	"""Generate a random 40-character API shared secret."""
	return frappe.generate_hash(length=40)


@frappe.whitelist()
def download_wordpress_plugin():
	"""Download the Woo Prime Connector WordPress plugin zip file."""
	plugin_path = frappe.get_app_path("woo_prime", "wordpress_plugin", "woo-prime-connector.zip")

	if not os.path.exists(plugin_path):
		frappe.throw(_("WordPress plugin package file not found."))

	with open(plugin_path, "rb") as f:
		file_content = f.read()

	frappe.response["filename"] = "woo-prime-connector.zip"
	frappe.response["filecontent"] = file_content
	frappe.response["type"] = "download"


def get_woo_api():
	"""Get WooCommerce API client instance."""
	from woo_prime.api.woo_api import WooAPI

	settings = frappe.get_single("Woo Settings")
	if not settings.enabled:
		frappe.throw("WooCommerce integration is not enabled. Please enable it in Woo Settings.")

	user_agent = getattr(settings, "api_user_agent", None)

	return WooAPI(
		url=settings.woo_site_url,
		consumer_key=settings.consumer_key,
		consumer_secret=settings.get_password("consumer_secret"),
		user_agent=user_agent,
	)



@frappe.whitelist()
def run_full_sync():
	"""Run full end-to-end sync: Sync Categories -> Fetch Products & Auto Link -> Sync Stock & Price."""
	from woo_prime.woo_prime.doctype.woo_category.woo_category import sync_categories_from_woo
	from woo_prime.woo_prime.doctype.woo_item.woo_item import fetch_items_from_woocommerce
	from woo_prime.api.sync import sync_all_stock, sync_all_prices

	cat_count = sync_categories_from_woo()
	item_res = fetch_items_from_woocommerce()
	sync_all_stock()
	sync_all_prices()

	summary = _(
		"✅ <b>Full Sync Completed Successfully!</b><br>"
		"📂 Categories Synced: <b>{0}</b><br>"
		"📦 Products Fetched: <b>{1}</b><br>"
		"🔗 SKU Auto-linked: <b>{2}</b><br>"
		"📊 Stock & Prices Pushed to WooCommerce."
	).format(
		cat_count,
		item_res.get("fetched", 0) if isinstance(item_res, dict) else 0,
		item_res.get("linked", 0) if isinstance(item_res, dict) else 0,
	)

	frappe.msgprint(summary, title=_("Full Sync Complete"), indicator="green")
	return summary


@frappe.whitelist()
def fetch_missing_order(woo_order_id):
	"""Fetch a specific order from WooCommerce by ID and sync it as a Sales Order in ERPNext."""
	if not woo_order_id:
		frappe.throw(_("Please provide a WooCommerce Order ID."))

	woo_order_id = str(woo_order_id).strip()
	api = get_woo_api()

	response = api.get(f"orders/{woo_order_id}")
	if response.status_code != 200:
		frappe.throw(_("Failed to fetch order #{0} from WooCommerce: {1}").format(woo_order_id, response.text[:200]))

	order_data = response.json()
	from woo_prime.api.sync import sync_order

	sync_order(order_data)

	so_name = frappe.db.get_value("Sales Order", {"woo_order_id": woo_order_id})
	if so_name:
		frappe.msgprint(
			_("✅ WooCommerce Order #{0} successfully synced to Sales Order <b>{1}</b>!").format(
				woo_order_id, so_name
			),
			title=_("Order Synced"),
			indicator="green",
		)
		return so_name
	else:
		frappe.msgprint(
			_("⚠️ Processed order payload, but Sales Order was not created (check Woo Sync Log for details)."),
			indicator="orange",
		)
		return None

