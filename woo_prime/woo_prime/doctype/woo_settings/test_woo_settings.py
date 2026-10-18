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

	def test_get_item_price_variant_fallback(self):
		import frappe
		from unittest.mock import patch
		from woo_prime.api.sync import _get_item_price

		def mock_get_value(doctype, filters, fieldname=None):
			# Variant item has no direct price, but is variant_of "TEMPLATE-ITEM"
			if doctype == "Item" and filters == "VARIANT-ITEM" and fieldname == "variant_of":
				return "TEMPLATE-ITEM"
			# Template item price is 450.0
			if doctype == "Item Price" and isinstance(filters, dict) and filters.get("item_code") == "TEMPLATE-ITEM":
				return 450.0
			return None

		with patch.object(frappe.db, "get_value", side_effect=mock_get_value):
			with patch.object(frappe, "get_single") as mock_settings:
				mock_settings.return_value.default_price_list = "Standard Selling"
				rate = _get_item_price("VARIANT-ITEM")
				self.assertEqual(rate, 450.0)

	def test_get_or_create_woo_attribute_id(self):
		from unittest.mock import MagicMock
		from woo_prime.api.sync import _get_or_create_woo_attribute_id, _ATTR_CACHE

		_ATTR_CACHE.clear()
		mock_api = MagicMock()
		mock_resp = MagicMock()
		mock_resp.status_code = 200
		mock_resp.json.return_value = [
			{"id": 5, "name": "Size", "slug": "pa_size", "type": "button"},
			{"id": 8, "name": "Color", "slug": "pa_color", "type": "color"},
		]
		mock_api.get.return_value = mock_resp

		attr_id, attr_name = _get_or_create_woo_attribute_id("Size", mock_api)
		self.assertEqual(attr_id, 5)
		self.assertEqual(attr_name, "Size")

		# Second lookup should use cache
		attr_id_cached, name_cached = _get_or_create_woo_attribute_id("size", mock_api)
		self.assertEqual(attr_id_cached, 5)
		self.assertEqual(name_cached, "Size")



