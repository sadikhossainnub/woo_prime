# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import os

import frappe
import requests
from frappe import _
from frappe.model.document import Document


class WooSettings(Document):
	def validate(self):
		if self.woo_site_url:
			# Remove trailing slash
			self.woo_site_url = self.woo_site_url.rstrip("/")

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
			else:
				frappe.msgprint(
					f"❌ Connection Failed!<br>Status Code: {response.status_code}<br>"
					f"Response: {response.text[:500]}",
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
		plugin_path = frappe.get_app_path("woo_prime", "wordpress_plugin", "woo_prime_connector.zip")

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
