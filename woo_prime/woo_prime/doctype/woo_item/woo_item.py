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


def _parse_bool(value):
	"""Parse various boolean representations to Python bool.
	
	Converts string representations like "true", "1", "yes" to True,
	and other values to False. Handles direct boolean values and None.
	
	Args:
		value: Input value to parse (bool, str, int, or None)
	
	Returns:
		bool: Parsed boolean value
	
	Examples:
		>>> _parse_bool(True)
		True
		>>> _parse_bool("true")
		True
		>>> _parse_bool("1")
		True
		>>> _parse_bool("yes")
		True
		>>> _parse_bool("false")
		False
		>>> _parse_bool("")
		False
	"""
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.lower() in ("true", "1", "yes")
	return bool(value)


def _parse_int(value, default=10):
	"""Parse integer value with default fallback.
	
	Attempts to convert the input value to an integer. If conversion fails
	(ValueError or TypeError), returns the specified default value.
	
	Args:
		value: Input value to parse (int, str, or other)
		default (int): Default value to return on parsing failure (default: 10)
	
	Returns:
		int: Parsed integer or default value
	
	Examples:
		>>> _parse_int(100)
		100
		>>> _parse_int("50")
		50
		>>> _parse_int("invalid", default=20)
		20
		>>> _parse_int(None, default=15)
		15
	"""
	try:
		return int(value)
	except (ValueError, TypeError):
		return default




def _is_within_sync_window():
	"""Check if current time is within the configured auto sync window.
	
	Returns:
		bool: True if within sync window or schedule is disabled, False otherwise
	
	Note:
		- If enable_auto_sync_schedule is False, always returns True (no restriction)
		- If times are not configured, always returns True
		- Silently returns False if outside window (no user notification)
		- Logs at INFO level when outside window for debugging
	"""
	settings = frappe.get_single("Woo Settings")
	
	# If schedule is not enabled, allow sync anytime
	if not getattr(settings, "enable_auto_sync_schedule", 0):
		return True
	
	start_time = getattr(settings, "auto_sync_start_time", None)
	end_time = getattr(settings, "auto_sync_end_time", None)
	
	# If times not configured, allow sync anytime
	if not start_time or not end_time:
		return True
	
	from datetime import datetime, time as dt_time
	
	# Get current time
	now = datetime.now().time()
	
	# Parse time strings if they're strings
	if isinstance(start_time, str):
		start_time = datetime.strptime(start_time, "%H:%M:%S").time()
	if isinstance(end_time, str):
		end_time = datetime.strptime(end_time, "%H:%M:%S").time()
	
	# Handle overnight windows (e.g., 22:00 to 06:00)
	if start_time <= end_time:
		# Normal case: start < end (e.g., 02:00 to 06:00)
		within_window = start_time <= now <= end_time
	else:
		# Overnight case: start > end (e.g., 22:00 to 06:00)
		within_window = now >= start_time or now <= end_time
	
	if not within_window:
		logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
		logger.info(
			f"[SyncWindow] Outside sync window (current: {now.strftime('%H:%M:%S')}, "
			f"window: {start_time.strftime('%H:%M:%S')} - {end_time.strftime('%H:%M:%S')}). "
			f"Skipping auto sync."
		)
	
	return within_window

def _publish_progress(user, operation, current_page, total_fetched, linked, created, skipped, failed, status, message):
	"""Publish real-time progress update for product fetch operations.
	
	Sends a realtime notification via Frappe's publish_realtime to update
	the user interface with current progress of the background job. The
	notification is sent immediately (after_commit=False) to provide
	responsive feedback.
	
	Args:
		user (str): Target user to receive the notification
		operation (str): Operation identifier ("fetch_products")
		current_page (int): Current page number being processed
		total_fetched (int): Total number of products fetched so far
		linked (int): Total number of products auto-linked to ERPNext Items
		created (int): Total number of new ERPNext Items created
		skipped (int): Total number of products skipped (e.g., empty products)
		failed (int): Total number of products that failed processing
		status (str): Current status ("starting", "fetching", "in_progress", "completed", "error")
		message (str): Human-readable status message for display
	
	Returns:
		None
	
	Note:
		Exceptions during publish are caught and logged to prevent
		disruption of the main background job execution.
	"""
	if not user:
		return
	
	try:
		frappe.publish_realtime(
			event="woo_fetch_progress",
			message={
				"operation": operation,
				"current_page": current_page,
				"total_fetched": total_fetched,
				"linked": linked,
				"created": created,
				"skipped": skipped,
				"failed": failed,
				"status": status,
				"message": message,
				"timestamp": frappe.utils.now()
			},
			user=user,
			after_commit=False
		)
	except Exception as e:
		frappe.logger("woo_prime").error(f"Failed to publish progress: {e}")


def _process_single_product(prod, settings, default_item_group, auto_create_missing, logger):
	"""Process a single product from WooCommerce response.
	
	Creates or updates a Woo Item record and optionally auto-links or creates
	the corresponding ERPNext Item. This function encapsulates the matching
	and linking logic for a single product.
	
	Args:
		prod (dict): Product data from WooCommerce API response
		settings (Document): Woo Settings singleton document
		default_item_group (str): Default Item Group name for new ERPNext Items
		auto_create_missing (bool): Whether to auto-create ERPNext Items for unmatched products
		logger (Logger): Frappe logger instance for logging operations
	
	Returns:
		dict: Result dictionary with keys:
			- "linked" (bool): True if Woo Item was linked to an ERPNext Item
			- "created" (bool): True if a new ERPNext Item was created
	
	Matching Logic:
		1. Find existing Woo Item by: woo_product_id, then sku, then primary key name
		2. Auto-link to ERPNext Item by: item_code match, then name match, then item_name match
		3. If matched: set item_code, set sync_status="Synced"
		4. If not matched and auto_create_missing=False: create new ERPNext Item
		5. If not matched and auto_create_missing=False: set sync_status="Not Synced"
	
	Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 10.1, 10.2, 10.3
	"""
	from frappe.utils import flt
	
	# Extract product data
	woo_id = prod.get("id")
	sku = (prod.get("sku") or "").strip()
	name = html.unescape(prod.get("name") or "")
	permalink = prod.get("permalink", "")
	description = html.unescape(prod.get("description", "") or "")
	short_description = html.unescape(prod.get("short_description", "") or "")
	
	# Generate Fake_SKU if SKU is empty (Requirement 8.7)
	if not sku:
		sku = f"WC-{woo_id}"
	
	# Find existing Woo Item (Requirements 10.1, 10.2)
	# Priority: woo_product_id, then sku field, then primary key name
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
	
	# Set Woo Item fields (Requirements 8.1, 8.2, 8.6)
	woo_item.woo_product_id = woo_id
	woo_item.woo_product_url = permalink
	woo_item.woo_description = description
	woo_item.woo_short_description = short_description
	woo_item.published = 1
	
	# Set prices using frappe.utils.flt() (Requirement 8.2)
	reg_p = flt(prod.get("regular_price") or 0)
	sale_p = flt(prod.get("sale_price") or 0)
	if reg_p > 0:
		woo_item.regular_price = reg_p
	if sale_p > 0:
		woo_item.sale_price = sale_p
	
	# Auto-link to ERPNext Item (Requirements 8.3, 8.4)
	# Priority: item_code match, then name match, then item_name match
	matched_item = (
		frappe.db.get_value("Item", {"item_code": sku}, "name")
		or frappe.db.get_value("Item", {"name": sku}, "name")
		or frappe.db.get_value("Item", {"item_name": name}, "name")
	)
	
	linked = False
	created = False
	
	if matched_item:
		# Item found: link it (Requirement 8.4)
		woo_item.item_code = matched_item
		woo_item.sync_status = "Synced"
		linked = True
	else:
		# No match found
		if auto_create_missing and not frappe.db.exists("Item", sku):
			# Create new ERPNext Item (Requirement 8.5)
			new_item = frappe.new_doc("Item")
			new_item.item_code = sku
			new_item.item_name = name or sku
			new_item.item_group = default_item_group
			new_item.stock_uom = "Nos"
			new_item.is_stock_item = 1
			if description:
				new_item.description = description
			new_item.insert(ignore_permissions=True)
			
			# Link to newly created Item (Requirement 8.5)
			woo_item.item_code = new_item.name
			woo_item.sync_status = "Synced"
			created = True
			linked = True
		elif not woo_item.item_code:
			# Not auto-creating and no match (Requirement 8.4)
			woo_item.sync_status = "Not Synced"
	
	# Save Woo Item (Requirement 8.2)
	woo_item.save(ignore_permissions=True)
	
	return {"linked": linked, "created": created}


@frappe.whitelist()
def fetch_items_from_woocommerce(auto_create_missing=False, batch_size=10, skip_empty_products=False, background=True):
	"""Fetch products from WooCommerce in batches (default 10 items per request), create/update Woo Item records, and auto-link to ERPNext Items by SKU.
	
	If background=True, enqueues execution into Frappe background worker and returns immediately.
	
	Args:
		auto_create_missing (bool): Auto-create ERPNext Items for unmatched products (default False)
		batch_size (int): Products per API page request (default 10)
		skip_empty_products (bool): Skip products with empty name or SKU (default False)
		background (bool): If True, enqueue to background; if False, run synchronously (default True)
	
	Returns:
		dict: Status and message if background=True, or fetched/linked/created counts if background=False
	"""
	import json as _json
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	# Parse parameters with string support
	auto_create_missing = _parse_bool(auto_create_missing)
	skip_empty_products = _parse_bool(skip_empty_products)
	background = _parse_bool(background)
	batch_size = _parse_int(batch_size, default=10)

	if background:
		# Enqueue background job with job deduplication
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce_background",
			auto_create_missing=auto_create_missing,
			batch_size=batch_size,
			skip_empty_products=skip_empty_products,
			user=frappe.session.user,
			queue="long",
			timeout=7200,
			enqueue_after_commit=True,
			job_name=f"woo_fetch_products_{frappe.session.user}",
		)
		msg = _("Product fetch started in background ({0} items per page). You can monitor progress in real-time.").format(batch_size)
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
	skipped_empty = 0

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

			# Skip empty products if enabled
			if skip_empty_products and (not sku or not name):
				logger.info(f"[FetchProducts] Skipping empty product ID {woo_id} (sku={sku}, name={name})")
				skipped_empty += 1
				continue

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
	if skipped_empty > 0:
		msg += _("<br>⏭️ Skipped <b>{0}</b> empty products.").format(skipped_empty)

	frappe.msgprint(msg, title=_("Fetch Complete"), indicator="green")
	return {"fetched": total_fetched, "linked": auto_linked, "created": created_erpnext_items, "skipped": skipped_empty}


def fetch_items_from_woocommerce_background(
	auto_create_missing=False,
	batch_size=10,
	skip_empty_products=False,
	user=None
):
	"""Background worker function for fetching products from WooCommerce.
	
	This function is executed by the background worker process.
	It performs pagination, per-page commits, per-product savepoints,
	and real-time progress updates.
	
	Args:
		auto_create_missing (bool): Auto-create ERPNext Items for unmatched products (default False)
		batch_size (int): Products per API page request
		skip_empty_products (bool): Skip products with empty name or SKU
		user (str): User who initiated the fetch (for realtime notifications)
	
	Returns:
		dict: Statistics (fetched, linked, created, skipped, failed counts)
	"""
	import json as _json
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	# Check if within sync window (silently skip if outside)
	if not _is_within_sync_window():
		return {
			"status": "skipped",
			"message": "Outside auto sync window",
			"fetched": 0,
			"linked": 0,
			"created": 0,
			"skipped": 0,
			"failed": 0
		}

	# Initialize logger with site-specific logging
	logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
	
	# Load Woo Settings and get default Item Group
	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None)
	if not default_item_group:
		default_item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	
	api = get_woo_api()
	
	# Initialize counters
	page = 1
	total_fetched = 0
	auto_linked = 0
	created_erpnext_items = 0
	skipped_empty = 0
	failed_products = 0
	
	# Send initial progress notification with status "starting"
	_publish_progress(
		user=user,
		operation="fetch_products",
		current_page=0,
		total_fetched=0,
		linked=0,
		created=0,
		skipped=0,
		failed=0,
		status="starting",
		message="Initializing product fetch..."
	)
	
	logger.info(f"[FetchProducts] Starting background fetch: auto_create_missing={auto_create_missing}, batch_size={batch_size}, skip_empty_products={skip_empty_products}")
	
	try:
		# Main pagination loop
		while True:
			req_params = {"per_page": batch_size, "page": page}
			logger.info(f"[FetchProducts] Page {page} — GET products | params={req_params}")
			
			# Send progress update
			_publish_progress(
				user=user,
				operation="fetch_products",
				current_page=page,
				total_fetched=total_fetched,
				linked=auto_linked,
				created=created_erpnext_items,
				skipped=skipped_empty,
				failed=failed_products,
				status="fetching",
				message=f"Fetching page {page}..."
			)
			
			# API request
			response = api.get("products", params=req_params)
			
			# Build request/response data for Woo Sync Log
			request_log = _json.dumps({
				"method": "GET",
				"endpoint": "products",
				"params": req_params,
				"page": page
			}, indent=2, default=str)
			
			resp_body_preview = (response.text or "")[:5000]
			response_log = _json.dumps({
				"status_code": response.status_code,
				"headers": dict(response.headers) if hasattr(response, "headers") else {},
				"body_preview": resp_body_preview
			}, indent=2, default=str)
			
			# Handle API errors
			if response.status_code != 200:
				error_details = (response.text or "").strip()[:300]
				if not error_details:
					reason = getattr(response, "reason", "No details returned")
					error_details = f"HTTP Status {response.status_code} ({reason})"
				
				logger.error(f"[FetchProducts] FAILED page {page} — HTTP {response.status_code}: {error_details}")
				
				# Log failed request
				create_log(
					sync_type="Item",
					direction="Incoming",
					status="Failed",
					request_data=request_log,
					response_data=response_log,
					error_message=f"HTTP {response.status_code}: {error_details}"
				)
				
				# Send error notification
				_publish_progress(
					user=user,
					operation="fetch_products",
					current_page=page,
					total_fetched=total_fetched,
					linked=auto_linked,
					created=created_erpnext_items,
					skipped=skipped_empty,
					failed=failed_products,
					status="error",
					message=f"API Error: {error_details}"
				)
				
				# Break pagination on API error
				break
			
			products = response.json()
			logger.info(f"[FetchProducts] Page {page} — received {len(products)} products")
			
			# Check for end of pagination
			if not products:
				break
			
			# Log successful batch fetch
			create_log(
				sync_type="Item",
				direction="Incoming",
				status="Success",
				request_data=request_log,
				response_data=response_log
			)
			
			# Process products with per-product savepoints
			page_linked = 0
			page_created = 0
			page_skipped = 0
			page_failed = 0
			
			for prod in products:
				woo_id = prod.get("id")
				sku = (prod.get("sku") or "").strip()
				name = html.unescape(prod.get("name") or "")
				
				# Skip empty products if enabled
				if skip_empty_products and (not sku or not name):
					logger.info(f"[FetchProducts] Skipping empty product ID {woo_id} (sku={sku}, name={name})")
					page_skipped += 1
					skipped_empty += 1
					continue
				
				# Process product with savepoint
				savepoint_name = f"product_{woo_id}"
				try:
					frappe.db.savepoint(savepoint_name)
					
					result = _process_single_product(
						prod=prod,
						settings=settings,
						default_item_group=default_item_group,
						auto_create_missing=auto_create_missing,
						logger=logger
					)
					
					# Update counters based on result
					if result["linked"]:
						page_linked += 1
						auto_linked += 1
					if result["created"]:
						page_created += 1
						created_erpnext_items += 1
					
					total_fetched += 1
					
				except Exception as prod_error:
					# Rollback to savepoint
					frappe.db.rollback(savepoint_name)
					
					# Log error with traceback
					error_traceback = frappe.get_traceback()
					logger.error(f"[FetchProducts] Product {woo_id} failed: {prod_error}\n{error_traceback}")
					
					# Create Individual_Error_Log
					create_log(
						sync_type="Item",
						direction="Incoming",
						status="Failed",
						woo_reference_id=str(woo_id),
						error_message=f"Product {woo_id} (SKU: {sku}): {str(prod_error)[:500]}"
					)
					
					page_failed += 1
					failed_products += 1
			
			# Commit page
			try:
				frappe.db.commit()
				logger.info(
					f"[FetchProducts] Page {page} committed: "
					f"{len(products)} products, {page_linked} linked, "
					f"{page_created} created, {page_skipped} skipped, {page_failed} failed"
				)
			except Exception as commit_error:
				logger.error(f"[FetchProducts] Commit failed for page {page}: {commit_error}")
				frappe.db.rollback()
				
				# Send error notification
				_publish_progress(
					user=user,
					operation="fetch_products",
					current_page=page,
					total_fetched=total_fetched,
					linked=auto_linked,
					created=created_erpnext_items,
					skipped=skipped_empty,
					failed=failed_products,
					status="error",
					message=f"Database commit failed: {str(commit_error)[:200]}"
				)
				break
			
			# Send progress update after page commit
			_publish_progress(
				user=user,
				operation="fetch_products",
				current_page=page,
				total_fetched=total_fetched,
				linked=auto_linked,
				created=created_erpnext_items,
				skipped=skipped_empty,
				failed=failed_products,
				status="in_progress",
				message=f"Page {page} complete ({len(products)} products processed)"
			)
			
			page += 1
		
		# Send final completion notification
		msg = _(
			"✅ Fetched <b>{0}</b> products from WooCommerce!<br>"
			"🔗 Automatically linked <b>{1}</b> items to ERPNext Item Master."
		).format(total_fetched, auto_linked)
		
		if created_erpnext_items > 0:
			msg += _("<br>✨ Created <b>{0}</b> new ERPNext Item Master records.").format(created_erpnext_items)
		
		if skipped_empty > 0:
			msg += _("<br>⏭️ Skipped <b>{0}</b> empty products.").format(skipped_empty)
		
		if failed_products > 0:
			msg += _("<br>⚠️ <b>{0}</b> products failed (see Woo Sync Log).").format(failed_products)
		
		_publish_progress(
			user=user,
			operation="fetch_products",
			current_page=page - 1,
			total_fetched=total_fetched,
			linked=auto_linked,
			created=created_erpnext_items,
			skipped=skipped_empty,
			failed=failed_products,
			status="completed",
			message=msg
		)
		
		logger.info(
			f"[FetchProducts] Completed: {total_fetched} fetched, {auto_linked} linked, "
			f"{created_erpnext_items} created, {skipped_empty} skipped, {failed_products} failed"
		)
		
		return {
			"status": "success",
			"fetched": total_fetched,
			"linked": auto_linked,
			"created": created_erpnext_items,
			"skipped": skipped_empty,
			"failed": failed_products
		}
		
	except Exception as e:
		# Fatal error
		error_traceback = frappe.get_traceback()
		logger.error(f"[FetchProducts] Fatal error: {e}\n{error_traceback}")
		
		# Send error notification
		_publish_progress(
			user=user,
			operation="fetch_products",
			current_page=page,
			total_fetched=total_fetched,
			linked=auto_linked,
			created=created_erpnext_items,
			skipped=skipped_empty,
			failed=failed_products,
			status="error",
			message=f"Fatal error: {str(e)[:200]}"
		)
		
		raise


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


