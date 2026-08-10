# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
import requests
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
