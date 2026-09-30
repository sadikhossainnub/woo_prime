# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import html
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class WooItem(Document):
	def validate(self):
		from frappe.utils import flt
		if self.item_code:
			# Fetch item_name if not set
			if not self.item_name:
				raw_name = frappe.db.get_value("Item", self.item_code, "item_name")
				self.item_name = html.unescape(raw_name) if raw_name else ""
			elif self.item_name:
				self.item_name = html.unescape(self.item_name)

			# Auto-fetch regular_price if empty
			if not self.regular_price:
				settings = frappe.get_single("Woo Settings")
				price_list = getattr(settings, "default_price_list", None) or "Standard Selling"
				from woo_prime.api.sync import _get_item_price
				price = _get_item_price(self.item_code, price_list)
				if price:
					self.regular_price = flt(price)

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
def fetch_items_from_woocommerce(auto_create_missing=True, batch_size=10, background=False):
	"""Fetch products from WooCommerce in batches (default 10 items per request), create/update Woo Item records, and auto-link to ERPNext Items by SKU.
	
	If background=True, enqueues execution into Frappe background worker and returns immediately.
	"""
	import json as _json
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	if isinstance(auto_create_missing, str):
		auto_create_missing = frappe.parse_json(auto_create_missing) if auto_create_missing.startswith("{") else (auto_create_missing.lower() in ("true", "1"))

	if isinstance(background, str):
		background = background.lower() in ("true", "1")

	try:
		batch_size = int(batch_size) if batch_size else 10
	except (ValueError, TypeError):
		batch_size = 10

	if background:
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce",
			auto_create_missing=auto_create_missing,
			batch_size=batch_size,
			background=False,
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
		)
		msg = _("Background product fetch started ({0} items per batch). You can monitor progress in Woo Sync Log.").format(batch_size)
		if frappe.request:
			frappe.msgprint(msg, title=_("Fetch Queued"), indicator="blue")
		return {
			"status": "queued",
			"message": msg,
		}

	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None)
	if not default_item_group:
		default_item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"

	logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
	api = get_woo_api()
	page = 1
	total_fetched = 0
	auto_linked = 0
	created_erpnext_items = 0

	while True:
		req_params = {"per_page": batch_size, "page": page}
		logger.info(f"[FetchProducts] Requesting page {page} — GET products | params={req_params}")

		response = api.get("products", params=req_params)

		# Build request/response data for Woo Sync Log
		request_log = _json.dumps({
			"method": "GET",
			"endpoint": "products",
			"params": req_params,
			"url": response.url if hasattr(response, "url") else "",
		}, indent=2, default=str)

		# Truncate response body to 5000 chars to avoid DB bloat
		resp_body_preview = (response.text or "")[:5000]
		response_log = _json.dumps({
			"status_code": response.status_code,
			"headers": dict(response.headers) if hasattr(response, "headers") else {},
			"body_preview": resp_body_preview,
		}, indent=2, default=str)

		if response.status_code != 200:
			error_details = (response.text or "").strip()[:300]
			if not error_details:
				reason = getattr(response, "reason", "No details returned")
				error_details = f"HTTP Status {response.status_code} ({reason})"

			logger.error(f"[FetchProducts] FAILED page {page} — HTTP {response.status_code}: {error_details}")

			# Log failed request to Woo Sync Log
			create_log(
				sync_type="Item",
				direction="Incoming",
				status="Failed",
				request_data=request_log,
				response_data=response_log,
				error_message=f"HTTP {response.status_code}: {error_details}",
			)
			frappe.throw(_("Failed to fetch products from WooCommerce: {0}").format(error_details))

		products = response.json()

		logger.info(f"[FetchProducts] Page {page} — HTTP {response.status_code}, received {len(products)} products")

		if not products:
			break

		# Log successful batch fetch to Woo Sync Log
		create_log(
			sync_type="Item",
			direction="Incoming",
			status="Success",
			request_data=request_log,
			response_data=response_log,
		)

		for prod in products:
			woo_id = prod.get("id")
			sku = (prod.get("sku") or "").strip()
			name = html.unescape(prod.get("name") or "")
			permalink = prod.get("permalink", "")
			description = html.unescape(prod.get("description", "") or "")
			short_description = html.unescape(prod.get("short_description", "") or "")

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

			from frappe.utils import flt
			reg_p = flt(prod.get("regular_price") or 0)
			sale_p = flt(prod.get("sale_price") or 0)
			if reg_p > 0:
				woo_item.regular_price = reg_p
			if sale_p > 0:
				woo_item.sale_price = sale_p

			# Auto-link to ERPNext Item by matching SKU / item_code / name
			matched_item = (
				frappe.db.get_value("Item", {"item_code": sku}, "name")
				or frappe.db.get_value("Item", {"name": sku}, "name")
				or frappe.db.get_value("Item", {"item_name": name}, "name")
			)

			if matched_item:
				woo_item.item_code = matched_item
				woo_item.sync_status = "Synced"
				auto_linked += 1
			else:
				if auto_create_missing and not frappe.db.exists("Item", sku):
					try:
						new_item = frappe.new_doc("Item")
						new_item.item_code = sku
						new_item.item_name = name or sku
						new_item.item_group = default_item_group
						new_item.stock_uom = "Nos"
						new_item.is_stock_item = 1
						if description:
							new_item.description = description
						new_item.insert(ignore_permissions=True)

						woo_item.item_code = new_item.name
						woo_item.sync_status = "Synced"
						created_erpnext_items += 1
						auto_linked += 1
					except Exception as err:
						frappe.log_error(title="Auto-create Item Error", message=str(err))
						woo_item.sync_status = "Not Synced"
				elif not woo_item.item_code:
					woo_item.sync_status = "Not Synced"

			woo_item.save(ignore_permissions=True)
			total_fetched += 1

		page += 1

	frappe.db.commit()
	msg = _("✅ Fetched <b>{0}</b> products from WooCommerce!<br>🔗 Automatically linked <b>{1}</b> items to ERPNext Item Master.").format(
		total_fetched, auto_linked
	)
	if created_erpnext_items > 0:
		msg += _("<br>✨ Created <b>{0}</b> new ERPNext Item Master records.").format(created_erpnext_items)

	frappe.msgprint(msg, title=_("Fetch Complete"), indicator="green")
	return {"fetched": total_fetched, "linked": auto_linked, "created": created_erpnext_items}


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


@frappe.whitelist()
def publish_item_from_item_master(item_code):
	"""Publish an ERPNext Item to WooCommerce directly from the Item Master form.

	Finds or auto-creates the linked Woo Item, then triggers publish_to_woocommerce.

	Args:
		item_code: ERPNext Item code

	Returns:
		dict with woo_product_id, woo_product_url, woo_item_name
	"""
	if not item_code or not frappe.db.exists("Item", item_code):
		frappe.throw(_("Item {0} not found.").format(item_code))

	item = frappe.get_doc("Item", item_code)

	# Find existing Woo Item linked to this item_code
	woo_item_name = frappe.db.get_value("Woo Item", {"item_code": item_code})

	if not woo_item_name:
		# Also try matching by SKU = item_code
		woo_item_name = frappe.db.get_value("Woo Item", {"sku": item_code})

	if woo_item_name:
		woo_item = frappe.get_doc("Woo Item", woo_item_name)
		# Ensure item_code is linked
		if not woo_item.item_code:
			woo_item.item_code = item_code
			woo_item.save(ignore_permissions=True)
	else:
		# Auto-create a Woo Item for this ERPNext Item
		settings = frappe.get_single("Woo Settings")
		price_list = getattr(settings, "default_price_list", None) or "Standard Selling"

		from woo_prime.api.sync import _get_item_price
		from frappe.utils import flt

		price = _get_item_price(item_code, price_list)

		woo_item = frappe.new_doc("Woo Item")
		woo_item.sku = item_code
		woo_item.item_code = item_code
		woo_item.item_name = html.unescape(item.item_name or item_code)
		woo_item.regular_price = flt(price) if price else 0
		if item.description:
			woo_item.woo_description = item.description
		woo_item.save(ignore_permissions=True)
		frappe.db.commit()

	# Now publish
	woo_item.publish_to_woocommerce()
	woo_item.reload()

	return {
		"woo_product_id": woo_item.woo_product_id,
		"woo_product_url": woo_item.woo_product_url or "",
		"woo_item_name": woo_item.name,
	}


