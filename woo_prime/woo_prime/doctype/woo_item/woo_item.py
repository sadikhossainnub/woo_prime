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


@frappe.whitelist()
def fetch_items_from_woocommerce():
	"""Fetch products from WooCommerce, create/update Woo Item records, and auto-link to ERPNext Items by SKU."""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	api = get_woo_api()
	page = 1
	total_fetched = 0
	auto_linked = 0

	while True:
		response = api.get("products", params={"per_page": 100, "page": page})
		if response.status_code != 200:
			frappe.throw(_("Failed to fetch products from WooCommerce: {0}").format(response.text[:300]))

		products = response.json()
		if not products:
			break

		for prod in products:
			woo_id = prod.get("id")
			sku = (prod.get("sku") or "").strip()
			name = prod.get("name")
			permalink = prod.get("permalink", "")
			description = prod.get("description", "")
			short_description = prod.get("short_description", "")

			if not sku:
				sku = f"WC-{woo_id}"

			# Find existing Woo Item by woo_product_id, sku field, or primary key name
			existing_name = (
				frappe.db.get_value("Woo Item", {"woo_product_id": woo_id})
				or frappe.db.get_value("Woo Item", {"sku": sku})
				or (frappe.db.exists("Woo Item", sku) and sku)
			)

			if existing_name:
				woo_item = frappe.get_doc("Woo Item", existing_name)
			else:
				woo_item = frappe.new_doc("Woo Item")
				woo_item.sku = sku

			woo_item.woo_product_id = woo_id
			woo_item.woo_product_url = permalink
			woo_item.woo_description = description
			woo_item.woo_short_description = short_description
			woo_item.published = 1

			# Auto-link to ERPNext Item by matching SKU / item_code
			matched_item = (
				frappe.db.get_value("Item", {"item_code": sku}, "name")
				or frappe.db.get_value("Item", {"name": sku}, "name")
			)

			if matched_item:
				woo_item.item_code = matched_item
				woo_item.sync_status = "Synced"
				auto_linked += 1
			elif not woo_item.item_code:
				woo_item.sync_status = "Not Synced"

			woo_item.save(ignore_permissions=True)
			total_fetched += 1

		page += 1

	frappe.db.commit()
	frappe.msgprint(
		_("✅ Fetched {0} products from WooCommerce!<br>🔗 Automatically linked {1} items to ERPNext by SKU.").format(
			total_fetched, auto_linked
		),
		title=_("Fetch Complete"),
		indicator="green",
	)
	return {"fetched": total_fetched, "linked": auto_linked}


@frappe.whitelist()
def auto_link_unlinked_items():
	"""Scan all Woo Items without item_code and automatically link them to ERPNext Items with matching SKU."""
	unlinked = frappe.get_all("Woo Item", filters={"item_code": ["in", ["", None]]}, fields=["name", "sku"])
	linked_count = 0

	for row in unlinked:
		sku = row.sku
		if not sku:
			continue

		matched_item = (
			frappe.db.get_value("Item", {"item_code": sku}, "name")
			or frappe.db.get_value("Item", {"name": sku}, "name")
		)

		if matched_item:
			doc = frappe.get_doc("Woo Item", row.name)
			doc.item_code = matched_item
			doc.sync_status = "Synced"
			doc.save(ignore_permissions=True)
			linked_count += 1

	frappe.db.commit()
	frappe.msgprint(
		_("✅ Auto-linked {0} Woo Items to ERPNext Items by SKU!").format(linked_count),
		title=_("Auto Link Complete"),
		indicator="green",
	)
	return linked_count


@frappe.whitelist()
def bulk_link_to_erpnext_item(items, target_item_code=None):
	"""Bulk link selected Woo Items to an ERPNext Item or match automatically by SKU."""
	import json

	if isinstance(items, str):
		items = json.loads(items)

	updated = 0
	for item_name in items:
		doc = frappe.get_doc("Woo Item", item_name)
		item_to_link = target_item_code

		if not item_to_link:
			# Auto match by SKU
			item_to_link = (
				frappe.db.get_value("Item", {"item_code": doc.sku}, "name")
				or frappe.db.get_value("Item", {"name": doc.sku}, "name")
			)

		if item_to_link and frappe.db.exists("Item", item_to_link):
			doc.item_code = item_to_link
			doc.sync_status = "Synced"
			doc.save(ignore_permissions=True)
			updated += 1

	frappe.db.commit()
	return updated


@frappe.whitelist()
def create_erpnext_items_from_woo(items):
	"""Bulk auto-create ERPNext Item records for Woo Items that don't have matching ERPNext items."""
	import json

	if isinstance(items, str):
		items = json.loads(items)

	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None)
	if not default_item_group:
		default_item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"

	created_count = 0

	for item_name in items:
		woo_item = frappe.get_doc("Woo Item", item_name)
		if woo_item.item_code and frappe.db.exists("Item", woo_item.item_code):
			continue

		target_code = (woo_item.sku or woo_item.name).strip()
		if not target_code:
			continue

		# Check if Item already exists in ERPNext
		if not frappe.db.exists("Item", target_code):
			new_item = frappe.new_doc("Item")
			new_item.item_code = target_code
			new_item.item_name = woo_item.name or target_code
			new_item.item_group = default_item_group
			new_item.stock_uom = "Nos"
			new_item.is_stock_item = 1
			if woo_item.woo_description:
				new_item.description = woo_item.woo_description
			new_item.insert(ignore_permissions=True)
			created_count += 1

		woo_item.item_code = target_code
		woo_item.sync_status = "Synced"
		woo_item.save(ignore_permissions=True)

	frappe.db.commit()
	frappe.msgprint(
		_("✅ Auto-created <b>{0}</b> ERPNext Item(s) and linked them to Woo Items!").format(created_count),
		title=_("Items Created"),
		indicator="green",
	)
	return created_count

