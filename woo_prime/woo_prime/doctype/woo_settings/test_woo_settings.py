# Copyright (c) 2026, prime tech bd and Contributors
# See license.txt

# import frappe
import unittest


class UnitTestWooSettings(unittest.TestCase):
	"""Integration tests for WooSettings and WooAPI helper functions."""

	def test_default_user_agent(self):
		from woo_prime.api.woo_api import DEFAULT_USER_AGENT
		self.assertEqual(DEFAULT_USER_AGENT, "curl/8.5.0")

	def test_cloudflare_response_detection(self):
		from woo_prime.api.woo_api import is_cloudflare_response
		from unittest.mock import MagicMock

		resp = MagicMock()
		resp.status_code = 403
		resp.text = "<html><head><title>Attention Required! | Cloudflare</title></head><body>Sorry, you have been blocked</body></html>"
		resp.headers = {"Server": "cloudflare"}

		self.assertTrue(is_cloudflare_response(resp))

	def test_valid_json_detection(self):
		from woo_prime.api.woo_api import is_valid_json, is_woocommerce_json_error
		from unittest.mock import MagicMock

		resp = MagicMock()
		resp.status_code = 400
		resp.text = '{"code": "woocommerce_rest_invalid_id", "message": "Invalid ID.", "data": {"status": 400}}'
		resp.json.return_value = {"code": "woocommerce_rest_invalid_id", "message": "Invalid ID.", "data": {"status": 400}}
		resp.headers = {"Content-Type": "application/json"}

		self.assertTrue(is_valid_json(resp))
		self.assertTrue(is_woocommerce_json_error(resp))

	def test_publish_item_from_item_master_nonexistent(self):
		import frappe
		from woo_prime.woo_prime.doctype.woo_item.woo_item import publish_item_from_item_master

		with self.assertRaises(frappe.ValidationError):
			publish_item_from_item_master("NON_EXISTENT_ITEM_CODE_99999")

