# Copyright (c) 2026, prime tech bd and Contributors
# See license.txt

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Provide mock frappe if not running within bench
if "frappe" not in sys.modules:
	mock_frappe = types.ModuleType("frappe")
	mock_frappe._ = lambda x: x
	mock_frappe.whitelist = lambda *args, **kwargs: (lambda f: f)
	mock_frappe.only_for = MagicMock()
	mock_frappe.throw = MagicMock(side_effect=Exception)
	mock_frappe.log_error = MagicMock()
	mock_frappe.logger = MagicMock(return_value=MagicMock())
	mock_frappe.get_single = MagicMock()
	mock_frappe.get_doc = MagicMock()
	mock_frappe.get_all = MagicMock(return_value=[])
	mock_frappe.db = MagicMock()
	mock_frappe.cache = MagicMock(return_value=MagicMock())
	mock_frappe.local = types.SimpleNamespace()
	sys.modules["frappe"] = mock_frappe
	sys.modules["frappe.model"] = types.ModuleType("frappe.model")
	sys.modules["frappe.model.document"] = types.ModuleType("frappe.model.document")
	sys.modules["frappe.model.document"].Document = type("Document", (), {})
	sys.modules["frappe.utils"] = types.ModuleType("frappe.utils")
	sys.modules["frappe.utils"].flt = lambda v, p=None: float(v or 0)
	sys.modules["frappe.utils"].cint = lambda v: int(v or 0)
	sys.modules["frappe.utils"].now_datetime = MagicMock(return_value="2026-11-16 12:00:00")
	sys.modules["frappe.utils"].get_datetime = MagicMock(return_value="2026-11-16 12:00:00")
	sys.modules["frappe.utils"].today = MagicMock(return_value="2026-11-16")
	sys.modules["frappe.utils"].add_days = MagicMock(return_value="2026-11-17")
	sys.modules["frappe.scrub"] = lambda s: s.lower().replace(" ", "_")

from woo_prime.api.sync import (
	_resolve_price_list,
	_should_skip_variant,
	_prepare_variation_payload,
	_publish_variation_data,
)


class TestVariableSyncFixes(unittest.TestCase):
	def setUp(self):
		import frappe
		frappe.local = types.SimpleNamespace()
		frappe.db.exists.return_value = False
		frappe.get_all.side_effect = None
		frappe.get_all.return_value = []

	def test_resolve_price_list_prefers_website_price_list(self):
		settings = types.SimpleNamespace(
			website_price_list="Website Price List",
			default_price_list="Standard Selling"
		)
		pl = _resolve_price_list(settings)
		self.assertEqual(pl, "Website Price List")

	def test_resolve_price_list_falls_back_to_default(self):
		settings = types.SimpleNamespace(
			website_price_list=None,
			default_price_list="Standard Selling"
		)
		pl = _resolve_price_list(settings)
		self.assertEqual(pl, "Standard Selling")

	def test_resolve_price_list_ultimate_fallback(self):
		settings = types.SimpleNamespace(
			website_price_list=None,
			default_price_list=None
		)
		pl = _resolve_price_list(settings)
		self.assertEqual(pl, "Standard Selling")

	def test_should_skip_variant_disabled_item(self):
		child_woo = types.SimpleNamespace(
			item_code="TP-01",
			sku="TP-01"
		)
		item = types.SimpleNamespace(item_code="TP-01", disabled=1)
		should_skip, reason = _should_skip_variant(child_woo, item, context="template", return_reason=True)
		self.assertTrue(should_skip)
		self.assertIn("disabled", reason)

	def test_image_resilience_retries_without_image(self):
		"""When variation creation fails with image, it should retry without image."""
		import frappe
		api = MagicMock()
		# First call with image fails, second call without image succeeds
		api.create_product_variation.side_effect = [
			Exception("Image download timeout"),
			{"id": 9999, "permalink": "https://example.com/product/var-9999"}
		]

		child_woo = types.SimpleNamespace(
			name="WOO-01",
			item_code="VAR-01",
			sku="VAR-01",
			regular_price=100.0,
			sale_price=None,
			manage_stock=1,
			woo_product_id=0,
			woo_description="",
			menu_order=0,
			published=0,
			woo_product_url="",
			sync_status="Not Synced",
			last_synced=None,
			flags=types.SimpleNamespace(),
			save=MagicMock()
		)
		v_item = types.SimpleNamespace(
			name="VAR-01",
			item_code="VAR-01",
			description="Test Variant",
			weight_per_unit=None,
			attributes=[]
		)
		settings = types.SimpleNamespace(
			website_price_list="Standard Selling",
			default_price_list="Standard Selling",
			default_warehouse="Stores - T"
		)

		with patch("woo_prime.api.sync._prepare_variation_payload") as mock_prep:
			mock_prep.return_value = {
				"sku": "VAR-01",
				"regular_price": "100.0",
				"manage_stock": True,
				"image": {"src": "https://example.com/bad-image.jpg"}
			}
			res = _publish_variation_data(1234, child_woo, v_item, api, settings, sync_images=True)
			self.assertIsNotNone(res)
			self.assertEqual(res.get("id"), 9999)
			self.assertEqual(api.create_product_variation.call_count, 2)
			# Second call should not have image in payload
			second_call_payload = api.create_product_variation.call_args_list[1][0][1]
			self.assertNotIn("image", second_call_payload)

	def test_zero_price_variation_skipped_and_logged(self):
		"""Variation with regular price <= 0 should be skipped and logged."""
		api = MagicMock()
		child_woo = types.SimpleNamespace(
			name="WOO-FREE",
			item_code="VAR-FREE",
			sku="VAR-FREE",
			regular_price=0.0,
			woo_product_id=0,
			save=MagicMock()
		)
		v_item = types.SimpleNamespace(name="VAR-FREE", item_code="VAR-FREE", attributes=[])
		settings = types.SimpleNamespace(default_price_list="Standard Selling")

		with patch("woo_prime.api.sync._prepare_variation_payload") as mock_prep:
			mock_prep.return_value = {
				"sku": "VAR-FREE",
				"regular_price": "0.0",
				"manage_stock": True
			}
			with patch("woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log.create_log") as mock_log:
				res = _publish_variation_data(1234, child_woo, v_item, api, settings)
				self.assertIsNone(res)
				self.assertEqual(api.create_product_variation.call_count, 0)
				mock_log.assert_called_once()
				self.assertIn("Failed", mock_log.call_args[1].get("status"))

	def test_find_broken_variable_products_detects_zero_variations(self):
		"""find_broken_variable_products identifies published templates with 0 WC variations."""
		import frappe
		from woo_prime.api.sync import find_broken_variable_products

		mock_template = {
			"name": "WOO-TEMP",
			"item_code": "TP00172",
			"sku": "TP00172",
			"woo_product_id": 322032,
			"sync_status": "Synced"
		}
		frappe.get_all.side_effect = lambda doctype, **kwargs: (
			[mock_template] if doctype == "Woo Item" else ["TP00172-36", "TP00172-38"]
		)

		mock_api = MagicMock()
		# Return 0 variations from WooCommerce
		mock_resp = MagicMock()
		mock_resp.status_code = 200
		mock_resp.json.return_value = []
		mock_api.get.return_value = mock_resp

		with patch("woo_prime.woo_prime.doctype.woo_settings.woo_settings.get_woo_api", return_value=mock_api):
			res = find_broken_variable_products()
			self.assertEqual(res.get("broken_count"), 1)
			self.assertEqual(res["broken_products"][0]["item_code"], "TP00172")
			self.assertEqual(res["broken_products"][0]["wc_variations_count"], 0)

	def test_repair_broken_products_sets_ignore_version(self):
		"""_run_repair_broken_products sets flags.ignore_version on Woo Item to prevent version conflicts."""
		import frappe
		from woo_prime.api.sync import _run_repair_broken_products

		mock_doc = MagicMock()
		mock_doc.sync_status = "Synced"
		mock_doc.flags = types.SimpleNamespace()
		frappe.get_doc.return_value = mock_doc

		targets = [{
			"item_code": "TP00172",
			"woo_item": "WOO-TP00172",
			"woo_product_id": 322032
		}]

		with patch("woo_prime.api.sync.publish_item_to_woo") as mock_pub:
			res = _run_repair_broken_products(targets)
			self.assertEqual(res.get("repaired"), 1)
			self.assertEqual(res.get("failed"), 0)
			self.assertTrue(getattr(mock_doc.flags, "ignore_version", False))
			mock_pub.assert_called_once_with(mock_doc, sync_images=0)

	def test_get_item_images_falls_back_to_variant_image(self):
		"""Template item with no direct image falls back to first variant's image."""
		import frappe
		from woo_prime.api.sync import _get_item_images

		item = types.SimpleNamespace(
			name="TP00172",
			image=None,
			has_variants=1
		)
		woo_item = types.SimpleNamespace(
			name="WOO-TP00172",
			image=None,
			images=[]
		)

		frappe.db.get_value.return_value = "/files/variant_kurti.jpg"
		frappe.get_all.return_value = []
		frappe.conf = types.SimpleNamespace(get=lambda k: "https://dressup.com.bd")
		frappe.cache().get_value.return_value = None

		images = _get_item_images(item, woo_item)
		self.assertTrue(len(images) > 0)
		self.assertIn("variant_kurti.jpg", images[0]["src"])

	def test_fetch_single_item_by_sku_success(self):
		"""fetch_single_item_by_sku retrieves product from WooCommerce and syncs it."""
		import frappe
		from woo_prime.woo_prime.doctype.woo_item.woo_item import fetch_single_item_by_sku

		mock_api = MagicMock()
		mock_resp = MagicMock()
		mock_resp.status_code = 200
		mock_resp.json.return_value = [{
			"id": 99999,
			"sku": "TEST-SKU-001",
			"name": "Test Kurti",
			"permalink": "https://dressup.com.bd/test",
			"regular_price": "1200",
			"sale_price": "1000",
			"manage_stock": True,
			"type": "simple",
			"images": [{"src": "https://dressup.com.bd/files/test.jpg"}]
		}]
		mock_api.get.return_value = mock_resp

		mock_doc = MagicMock()
		mock_doc.name = "WOO-TEST-SKU-001"
		mock_doc.item_code = "TEST-SKU-001"
		mock_doc.sync_status = "Synced"
		mock_doc.flags = types.SimpleNamespace()

		frappe.db.get_value.return_value = "TEST-SKU-001"
		frappe.db.exists.return_value = True
		frappe.get_doc.return_value = mock_doc

		with patch("woo_prime.woo_prime.doctype.woo_settings.woo_settings.get_woo_api", return_value=mock_api):
			res = fetch_single_item_by_sku("TEST-SKU-001")
			self.assertEqual(res.get("status"), "success")
			self.assertEqual(res.get("sku"), "TEST-SKU-001")
			self.assertEqual(res.get("woo_product_id"), 99999)

	def test_sync_parse_bool(self):
		from woo_prime.api.sync import _parse_bool
		self.assertTrue(_parse_bool(True))
		self.assertTrue(_parse_bool("true"))
		self.assertTrue(_parse_bool("1"))
		self.assertTrue(_parse_bool(1))
		self.assertFalse(_parse_bool(False))
		self.assertFalse(_parse_bool("false"))
		self.assertFalse(_parse_bool("0"))
		self.assertFalse(_parse_bool(0))
		self.assertFalse(_parse_bool(None))



if __name__ == "__main__":
	unittest.main()
