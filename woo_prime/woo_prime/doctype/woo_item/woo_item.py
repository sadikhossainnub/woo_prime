# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class WooItem(Document):
	def validate(self):
		if self.item_code:
			# Fetch item_name if not set
			if not self.item_name:
				self.item_name = frappe.db.get_value("Item", self.item_code, "item_name")

	@frappe.whitelist()
	def publish_to_woocommerce(self):
		"""Create or update product on WooCommerce."""
		from woo_prime.api.sync import publish_item_to_woo

		try:
			result = publish_item_to_woo(self)
			self.reload()
			self.woo_product_id = result.get("id")
			self.published = 1
			self.woo_product_url = result.get("permalink", "")
			self.sync_status = "Synced"
			self.last_synced = now_datetime()
			self.save(ignore_permissions=True)

			# Log success
			create_sync_log(
				sync_type="Item",
				direction="Outgoing",
				status="Success",
				reference_doctype="Woo Item",
				reference_name=self.name,
				woo_reference_id=str(self.woo_product_id),
			)

			frappe.msgprint(
				_("✅ Product published to WooCommerce successfully!<br>Product ID: <b>{0}</b>").format(
					self.woo_product_id
				),
				title=_("Published"),
				indicator="green",
			)
		except Exception as e:
			try:
				self.reload()
			except Exception:
				pass
			self.sync_status = "Error"
			self.last_synced = now_datetime()
			self.save(ignore_permissions=True)

			create_sync_log(
				sync_type="Item",
				direction="Outgoing",
				status="Failed",
				reference_doctype="Woo Item",
				reference_name=self.name,
				error_message=str(e),
			)

			frappe.throw(_("Failed to publish to WooCommerce: {0}").format(str(e)))

	@frappe.whitelist()
	def sync_stock_now(self):
		"""Push current stock qty to WooCommerce."""
		from woo_prime.api.sync import sync_stock_to_woo

		if not self.woo_product_id:
			frappe.throw(_("This item has not been published to WooCommerce yet."))

		try:
			sync_stock_to_woo(self)
			self.last_synced = now_datetime()
			self.sync_status = "Synced"
			self.save()

			frappe.msgprint(
				_("✅ Stock synced to WooCommerce successfully!"),
				title=_("Stock Synced"),
				indicator="green",
			)
		except Exception as e:
			create_sync_log(
				sync_type="Stock",
				direction="Outgoing",
				status="Failed",
				reference_doctype="Woo Item",
				reference_name=self.name,
				woo_reference_id=str(self.woo_product_id or ""),
				error_message=str(e),
			)
			frappe.throw(_("Stock sync failed: {0}").format(str(e)))

	@frappe.whitelist()
	def sync_price_now(self):
		"""Push price to WooCommerce."""
		from woo_prime.api.sync import sync_price_to_woo

		if not self.woo_product_id:
			frappe.throw(_("This item has not been published to WooCommerce yet."))

		try:
			sync_price_to_woo(self)
			self.last_synced = now_datetime()
			self.sync_status = "Synced"
			self.save()

			frappe.msgprint(
				_("✅ Price synced to WooCommerce successfully!"),
				title=_("Price Synced"),
				indicator="green",
			)
		except Exception as e:
			create_sync_log(
				sync_type="Price",
				direction="Outgoing",
				status="Failed",
				reference_doctype="Woo Item",
				reference_name=self.name,
				woo_reference_id=str(self.woo_product_id or ""),
				error_message=str(e),
			)
			frappe.throw(_("Price sync failed: {0}").format(str(e)))


def create_sync_log(**kwargs):
	"""Helper to create a Woo Sync Log entry."""
	try:
		log = frappe.new_doc("Woo Sync Log")
		log.update(kwargs)
		log.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error("Woo Sync Log Creation Failed")


@frappe.whitelist()
def bulk_publish(items):
	"""Bulk publish multiple Woo Items to WooCommerce."""
	import json

	if isinstance(items, str):
		items = json.loads(items)

	success_count = 0
	fail_count = 0

	for item_name in items:
		try:
			woo_item = frappe.get_doc("Woo Item", item_name)
			woo_item.publish_to_woocommerce()
			success_count += 1
		except Exception:
			fail_count += 1
			frappe.log_error(f"Bulk publish failed for {item_name}")

	return {"success": success_count, "failed": fail_count}
