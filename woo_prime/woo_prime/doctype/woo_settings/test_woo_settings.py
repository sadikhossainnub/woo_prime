# Copyright (c) 2026, prime tech bd and Contributors
# See license.txt

# import frappe
import unittest

try:
	from frappe.tests.utils import FrappeTestCase
except ImportError:
	FrappeTestCase = unittest.TestCase


class UnitTestWooSettings(FrappeTestCase):
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
		import frappe
		from unittest.mock import MagicMock, patch
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

		with patch.object(frappe.db, "exists", return_value=False), \
			 patch.object(frappe.db, "get_value", return_value=None):
			attr_id, attr_name = _get_or_create_woo_attribute_id("Size", mock_api)
			self.assertEqual(attr_id, 5)
			self.assertEqual(attr_name, "Size")

			# Second lookup should use cache
			attr_id_cached, name_cached = _get_or_create_woo_attribute_id("size", mock_api)
			self.assertEqual(attr_id_cached, 5)
			self.assertEqual(name_cached, "Size")

	def test_calculate_cart_price_rate_rule(self):
		import frappe
		import json
		from unittest.mock import patch, MagicMock
		from woo_prime.api.price import calculate_cart_price

		passed_rule_args = {}

		def mock_get_pricing_rule(rule_args):
			nonlocal passed_rule_args
			passed_rule_args = rule_args
			return {
				"pricing_rule": "Special Promo Rate",
				"rate": 75.0,
				"pricing_rule_for": "Rate",
			}

		with patch("woo_prime.api.price.verify_api_secret"), \
			 patch.object(frappe.db, "exists", return_value=True), \
			 patch.object(frappe.db, "get_value", return_value="USD"), \
			 patch.object(frappe.db, "get_single_value", return_value="All Territories"), \
			 patch.object(frappe, "get_single") as mock_settings:

			mock_settings.return_value.default_price_list = "Standard Selling"
			mock_settings.return_value.default_company = "Test Co"

			cart_data = json.dumps([{"item_code": "TEST-ITEM-1", "qty": 2, "rate": 100.0}])

			with patch("erpnext.accounts.doctype.pricing_rule.pricing_rule.get_pricing_rule_for_item", side_effect=mock_get_pricing_rule):
				res = calculate_cart_price(cart_data=cart_data)

			self.assertEqual(res["status"], "success")
			self.assertEqual(len(res["items"]), 1)
			item = res["items"][0]
			self.assertEqual(item["original_rate"], 100.0)
			self.assertEqual(item["final_rate"], 75.0)
			self.assertEqual(item["discount_per_unit"], 25.0)
			self.assertEqual(item["total_discount"], 50.0)
			self.assertEqual(item["pricing_rule"], "Special Promo Rate")

			# Verify rule_args keys
			self.assertEqual(passed_rule_args.get("doctype"), "Sales Order")
			self.assertEqual(passed_rule_args.get("parenttype"), "Sales Order")
			self.assertIsNone(passed_rule_args.get("parent"))
			self.assertIsNone(passed_rule_args.get("name"))
			self.assertEqual(passed_rule_args.get("conversion_rate"), 1.0)
			self.assertEqual(passed_rule_args.get("plc_conversion_rate"), 1.0)
			self.assertEqual(passed_rule_args.get("transaction_type"), "selling")
			self.assertEqual(passed_rule_args.get("ignore_pricing_rule"), 0)
			self.assertIn("customer_group", passed_rule_args)
			self.assertIn("territory", passed_rule_args)

	def test_sync_order_status_filtering(self):
		import frappe
		from unittest.mock import patch, MagicMock
		from woo_prime.api.sync import sync_order

		with patch.object(frappe, "get_single") as mock_settings, \
			 patch("woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log.create_log") as mock_log:
			mock_settings.return_value.enabled = 1
			mock_settings.return_value.sync_order_statuses = "processing,completed"
			mock_settings.return_value.integration_user = "Administrator"

			order_data = {"id": 99991, "status": "pending", "total": "100.00"}
			result = sync_order(order_data)
			self.assertIsNone(result)
			self.assertTrue(mock_log.called)
			call_kwargs = mock_log.call_args.kwargs
			self.assertEqual(call_kwargs.get("status"), "Skipped")
			self.assertEqual(call_kwargs.get("woo_reference_id"), "99991")

	def test_sync_order_duplicate_idempotency(self):
		import frappe
		from unittest.mock import patch
		from woo_prime.api.sync import sync_order

		with patch.object(frappe, "get_single") as mock_settings, \
			 patch.object(frappe.db, "get_value", return_value="SO-EXISTING-001"):
			mock_settings.return_value.enabled = 1
			mock_settings.return_value.sync_order_statuses = "processing,completed"
			mock_settings.return_value.integration_user = "Administrator"

			order_data = {"id": 12345, "status": "processing", "total": "50.00"}
			result = sync_order(order_data)
			self.assertEqual(result, "SO-EXISTING-001")

	def test_sync_order_unmapped_item_handling(self):
		import frappe
		from unittest.mock import patch
		from woo_prime.api.sync import sync_order

		with patch.object(frappe, "get_single") as mock_settings, \
			 patch.object(frappe.db, "get_value", return_value=None), \
			 patch.object(frappe.db, "exists", return_value=False), \
			 patch("woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log.create_log") as mock_log:
			mock_settings.return_value.enabled = 1
			mock_settings.return_value.sync_order_statuses = "processing,completed"
			mock_settings.return_value.integration_user = "Administrator"

			order_data = {
				"id": 99992,
				"status": "processing",
				"total": "100.00",
				"line_items": [{"id": 1, "product_id": 888, "name": "Unknown Product", "quantity": 1, "subtotal": "100.00"}],
			}
			with self.assertRaises(Exception):
				sync_order(order_data)
			failed_calls = [c for c in mock_log.call_args_list if c.kwargs.get("status") == "Failed"]
			self.assertTrue(len(failed_calls) > 0)

	def test_sync_order_totals_with_coupon_shipping_fee(self):
		import frappe
		from unittest.mock import patch, MagicMock
		from woo_prime.api.sync import sync_order

		mock_so = MagicMock()
		mock_so.name = "SO-2026-99993"
		mock_so.grand_total = 205.0
		mock_so.items = []
		mock_so.taxes = []
		mock_so.loyalty_amount = 0
		mock_so.loyalty_program = None
		mock_so.loyalty_redemption_account = None

		def mock_get_value(doctype, filters, fieldname=None, as_dict=False):
			if doctype == "Sales Order" and isinstance(filters, dict) and "woo_order_id" in filters:
				return None
			if doctype == "Item" and filters == "ITEM001":
				return "ITEM001"
			if doctype == "Woo Item" and isinstance(filters, dict) and filters.get("woo_product_id") == 101:
				return "ITEM001"
			if doctype == "Customer":
				return "Test Customer"
			return None

		def mock_exists(doctype, filters=None):
			if doctype == "Sales Order":
				return False
			return True

		with patch.object(frappe, "get_single") as mock_settings, \
			 patch.object(frappe.db, "get_value", side_effect=mock_get_value), \
			 patch.object(frappe.db, "exists", side_effect=mock_exists), \
			 patch.object(frappe.db, "savepoint"), \
			 patch.object(frappe, "new_doc", return_value=mock_so), \
			 patch.object(frappe, "get_meta") as mock_meta, \
			 patch("woo_prime.api.sync._get_or_create_customer", return_value=("Test Customer", "Bill-1", "Ship-1")), \
			 patch("woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log.create_log"):

			mock_meta.return_value.has_field.return_value = True
			mock_settings.return_value.enabled = 1
			mock_settings.return_value.sync_order_statuses = "processing,completed"
			mock_settings.return_value.order_total_tolerance = 1.0
			mock_settings.return_value.default_company = "Test Co"
			mock_settings.return_value.default_warehouse = "Stores - TC"
			mock_settings.return_value.integration_user = "Administrator"
			mock_settings.return_value.auto_submit_orders = 0

			order_data = {
				"id": 99993,
				"status": "processing",
				"total": "205.00",
				"discount_total": "20.00",
				"shipping_total": "15.00",
				"billing": {"first_name": "John", "last_name": "Doe", "email": "john@example.com"},
				"shipping": {"first_name": "John", "last_name": "Doe"},
				"line_items": [
					{"id": 1, "product_id": 101, "sku": "ITEM001", "name": "Test Item", "quantity": 2, "subtotal": "200.00", "price": "100.00"}
				],
				"shipping_lines": [
					{"method_id": "flat_rate", "method_title": "Standard Shipping", "total": "15.00"}
				],
				"fee_lines": [
					{"name": "Handling Fee", "total": "10.00"}
				],
				"coupon_lines": [
					{"code": "PROMO20", "discount": "20.00"}
				],
			}

			result = sync_order(order_data)
			self.assertEqual(result, mock_so.name)
			self.assertEqual(mock_so.discount_amount, 20.0)
			self.assertEqual(mock_so.apply_discount_on, "Net Total")

	def test_price_guard_zero(self):
		import frappe
		from unittest.mock import patch, MagicMock
		from woo_prime.api.sync import sync_price_to_woo

		mock_woo_item = MagicMock()
		mock_woo_item.name = "WI-001"
		mock_woo_item.item_code = "ITEM-ZERO"
		mock_woo_item.regular_price = 0
		mock_woo_item.published = 1
		mock_woo_item.woo_product_id = 555
		mock_woo_item.sync_price = 1

		with patch.object(frappe, "get_single") as mock_settings, \
			 patch("woo_prime.api.sync._get_item_price", return_value=0), \
			 patch("woo_prime.woo_prime.doctype.woo_settings.woo_settings.get_woo_api") as mock_get_api:
			mock_settings.return_value.enabled = 1
			mock_settings.return_value.enable_price_sync = 1

			sync_price_to_woo(mock_woo_item)
			mock_get_api.return_value.update_price.assert_not_called()

	def test_woo_api_404_handling(self):
		import frappe
		from unittest.mock import patch, MagicMock
		from woo_prime.api.woo_api import WooAPI
		from woo_prime.api.sync import sync_stock_to_woo

		api = WooAPI("https://example.com", "ck_test", "cs_test")
		mock_resp = MagicMock()
		mock_resp.status_code = 404
		mock_resp.text = '{"code": "woocommerce_rest_product_invalid_id", "message": "Invalid ID."}'

		with patch.object(api, "put", return_value=mock_resp), \
			 patch.object(api, "create_product") as mock_create:
			with self.assertRaises(frappe.ValidationError):
				api.update_stock(99999, 10)
			# Must NEVER call create_product on 404 for stock update
			mock_create.assert_not_called()

		mock_woo_item = MagicMock()
		mock_woo_item.name = "WI-404"
		mock_woo_item.item_code = "ITEM-404"
		mock_woo_item.published = 1
		mock_woo_item.woo_product_id = 99999
		mock_woo_item.sync_stock = 1

		mock_api_inst = MagicMock()
		mock_api_inst.update_stock.side_effect = frappe.ValidationError("Product #99999 not found on WooCommerce (404)")

		with patch.object(frappe, "get_single") as mock_settings, \
			 patch.object(frappe.db, "get_value", return_value=None), \
			 patch("woo_prime.api.sync._get_stock_qty", return_value=5), \
			 patch("woo_prime.woo_prime.doctype.woo_settings.woo_settings.get_woo_api", return_value=mock_api_inst):

			sync_stock_to_woo(mock_woo_item)
			self.assertEqual(mock_woo_item.published, 0)
			self.assertEqual(mock_woo_item.sync_status, "Error")

	def test_webhook_signature_401(self):
		import frappe
		from unittest.mock import patch, MagicMock
		from woo_prime.api.webhook import verify_webhook_signature

		mock_req = MagicMock()
		mock_req.headers = {"X-WC-Webhook-Signature": "invalid_signature"}
		with patch.object(frappe, "get_single") as mock_settings, \
			 patch.object(frappe, "request", mock_req):
			mock_settings.return_value.get_password.return_value = "secret123"

			with self.assertRaises(frappe.AuthenticationError):
				verify_webhook_signature('{"id": 1}')

	def test_woo_sync_log_request_response_data_formatting(self):
		import frappe
		from unittest.mock import patch, MagicMock
		from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

		mock_log_doc = MagicMock()
		with patch.object(frappe, "new_doc", return_value=mock_log_doc), \
			 patch.object(frappe.db, "get_value", return_value=None):

			create_log(
				sync_type="Stock",
				direction="Outgoing",
				status="Success",
				reference_doctype="Woo Item",
				reference_name="WI-001",
				woo_reference_id="101",
				request_data={"item_code": "ITEM001", "stock_quantity": 10},
				response_data={"stock_quantity": 10},
			)
			self.assertIn('"item_code": "ITEM001"', mock_log_doc.request_data)
			self.assertIn('"stock_quantity": 10', mock_log_doc.response_data)




