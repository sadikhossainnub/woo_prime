# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import hmac
import frappe
from frappe import _


def verify_api_secret():
	"""Raise frappe.AuthenticationError unless the request carries the shared secret."""
	settings = frappe.get_single("Woo Settings")
	configured_secret = settings.get_password("api_shared_secret")

	if not configured_secret or not configured_secret.strip():
		frappe.throw(
			_("API Shared Secret is not configured in Woo Settings."),
			frappe.AuthenticationError,
		)

	received_token = frappe.request.headers.get("X-Woo-Prime-Token", "")
	if not received_token:
		auth_header = frappe.request.headers.get("Authorization", "")
		if auth_header.startswith("Bearer "):
			received_token = auth_header[7:].strip()

	if not received_token or not hmac.compare_digest(configured_secret.strip(), received_token.strip()):
		frappe.throw(
			_("Invalid API token or authentication failed."),
			frappe.AuthenticationError,
		)
