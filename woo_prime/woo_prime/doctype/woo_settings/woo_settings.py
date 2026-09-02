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
	def test_connection(self):
		"""Test WooCommerce API connection."""
		try:
			api = get_woo_api()
			response = api.get("system_status")
			if response.status_code == 200:
				data = response.json()
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

			# Fallback test with products endpoint
			prod_response = api.get("products", params={"per_page": 1})
			if prod_response.status_code == 200:
				frappe.msgprint(
					"✅ Connection Successful!<br>"
					"Successfully connected to WooCommerce REST API (Products endpoint).",
					title="Connection Test",
					indicator="green",
				)
				return

			msg = f"❌ Connection Failed!<br>Status Code: {response.status_code}<br>"
			resp_text = response.text or ""
			if "cloudflare" in resp_text.lower() or "attention required" in resp_text.lower():
				msg += (
					"<br><b>Troubleshooting Cloudflare Block (403 Forbidden):</b><br>"
					"Cloudflare WAF or Bot Protection is intercepting requests before reaching WordPress.<br>"
					"1. <b>Create Cloudflare WAF Rule:</b> In Cloudflare Dashboard → Security → WAF → Custom Rules, add a rule:<br>"
					"   Field: <i>URI Path</i> starts with <code>/wp-json/</code> → Action: <b>Skip</b> (WAF, Bot Fight Mode, Browser Integrity Check).<br>"
					"2. <b>Allow Server IP:</b> Go to Cloudflare → Security → WAF → Tools → IP Access Rules. Add your ERPNext Server IP to <b>Allow</b> list.<br>"
					"3. <b>Check REST API Key Permissions:</b> Ensure Key has <b>Read/Write</b> permissions in WP Admin → WooCommerce → Settings → Advanced → REST API.<br><br>"
				)
			elif response.status_code == 404:
				msg += (
					"<br><b>Troubleshooting 404 Not Found:</b><br>"
					"1. <b>WordPress Permalinks:</b> Go to WP Admin → Settings → Permalinks and change structure from <i>'Plain'</i> to <i>'Post name'</i>.<br>"
					"2. <b>WooCommerce Plugin:</b> Verify WooCommerce is installed and active.<br>"
					"3. <b>Site URL:</b> Ensure <i>Woo Site URL</i> (e.g. <code>http://demo.ptb18.xyz</code>) is entered correctly.<br>"
					"4. <b>Apache Config:</b> Ensure <code>mod_rewrite</code> is enabled and <code>AllowOverride All</code> is set in Apache.<br><br>"
				)
			elif response.status_code in (401, 403):
				msg += (
					"<br><b>Troubleshooting Auth Error (401 / 403):</b><br>"
					"1. <b>REST API Key Permissions:</b> Go to WP Admin → WooCommerce → Settings → Advanced → REST API. Edit your API Key and ensure permissions are set to <b>Read/Write</b>.<br>"
					"2. <b>User Account Permissions:</b> Ensure the WordPress user associated with the REST API Key has <b>Administrator</b> or <b>Shop Manager</b> role.<br>"
					"3. <b>Check Credentials:</b> Verify there are no trailing/leading spaces in Consumer Key or Consumer Secret.<br>"
					"4. <b>Apache Authorization Header:</b> If your web server strips HTTP Authorization headers, add the following to your WordPress <code>.htaccess</code> file:<br>"
					"<code>SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1</code> or <code>CGIPassAuth On</code><br><br>"
				)
			msg += f"Response: {resp_text[:500]}"
			frappe.msgprint(
				msg,
				title="Connection Test",
				indicator="red",
			)
		except Exception as e:
			frappe.msgprint(
				f"❌ Connection Failed!<br>Error: {str(e)}",
				title="Connection Test",
				indicator="red",
			)


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

	return WooAPI(
		url=settings.woo_site_url,
		consumer_key=settings.consumer_key,
		consumer_secret=settings.get_password("consumer_secret"),
	)
