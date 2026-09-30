# Design Document: Background Job Conversion for WooCommerce Fetch Operations

## 1. Overview

This design describes the conversion of synchronous WooCommerce fetch operations (products and categories) into asynchronous background jobs with robust progress tracking, error handling, and database transaction management.

### 1.1 Goals

- Convert product fetch (`fetch_items_from_woocommerce`) to background job execution
- Convert category fetch (`sync_categories_from_woo`) to background job execution
- Implement per-page database commits for reliability
- Add per-product savepoint-based error recovery
- Provide real-time progress notifications via Frappe's realtime pub/sub
- Add "skip empty products" option with new Woo Settings field
- Preserve existing matching and linking behavior
- Update UI with enqueue buttons and progress indicators

### 1.2 Non-Goals

- Refactoring unrelated sync code (order sync, stock sync, price sync)
- Changing the WooCommerce API client authentication or retry logic
- Modifying existing Woo Item or Woo Category DocType schemas

---

## 2. Architecture

### 2.1 Background Job Queue Strategy

All fetch operations will use Frappe's `enqueue()` function with the following configuration:

```python
frappe.enqueue(
	method="woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce_background",
	queue="long",  # Use long queue for extended operations
	timeout=7200,  # 2 hours max (120 minutes)
	enqueue_after_commit=True,  # Ensure transaction commit before enqueue
	job_name="woo_fetch_products",  # Unique job identifier
	**kwargs  # Pass parameters
)
```

**Queue Selection:**
- `long` queue: For operations expected to take >5 minutes
- Frappe's default worker configuration handles long-running jobs
- Jobs on the long queue can run up to the configured timeout

**Job Deduplication:**
- Use `job_name` parameter to prevent duplicate concurrent jobs
- If a job with the same `job_name` is already queued or running, new enqueue attempts will be rejected
- This prevents users from accidentally starting multiple fetch operations

### 2.2 Progress Tracking Architecture

**Real-time Updates via Frappe Publish:**

```python
# In background job function
frappe.publish_realtime(
	event="woo_fetch_progress",
	message={
		"operation": "fetch_products",
		"current_page": page,
		"total_fetched": total_fetched,
		"linked": auto_linked,
		"created": created_erpnext_items,
		"status": "in_progress",
		"progress_percent": progress_percent,
		"message": f"Processing page {page}..."
	},
	user=frappe.session.user,
	after_commit=False  # Send immediately
)
```

**Client-side Real-time Subscription (JavaScript):**

```javascript
// In woo_settings.js
frappe.realtime.on("woo_fetch_progress", (data) => {
	// Update progress bar
	// Update status message
	// Update counters
});
```

### 2.3 Database Transaction Management

#### 2.3.1 Per-Page Commits

After successfully fetching and processing each page of products/categories:

```python
# After processing all products in current page
try:
	frappe.db.commit()
	logger.info(f"[FetchProducts] Page {page} committed successfully")
except Exception as commit_error:
	logger.error(f"[FetchProducts] Commit failed for page {page}: {commit_error}")
	frappe.db.rollback()
	# Continue to next page or retry based on error handling strategy
```

**Benefits:**
- Partial progress is saved even if later pages fail
- Reduces memory footprint for large datasets
- Enables resumability from last successful page
- Prevents loss of work on timeout or crash

#### 2.3.2 Per-Product Savepoints

Within each page, use database savepoints for individual product processing:

```python
for prod in products:
	savepoint_name = f"product_{prod.get('id')}"
	try:
		# Create savepoint before processing product
		frappe.db.savepoint(savepoint_name)
		
		# Process product (create/update Woo Item, auto-link, etc.)
		_process_single_product(prod, settings, logger)
		
		# Implicit savepoint release on success
		
	except Exception as prod_error:
		# Rollback to savepoint (only this product)
		frappe.db.rollback(savepoint_name)
		
		# Log error but continue processing other products
		logger.error(f"[FetchProducts] Product {prod.get('id')} failed: {prod_error}")
		create_log(
			sync_type="Item",
			direction="Incoming",
			status="Failed",
			woo_reference_id=str(prod.get('id')),
			error_message=str(prod_error)
		)
		
		failed_count += 1
```

**Benefits:**
- Individual product errors don't fail the entire page
- Fine-grained error tracking per product
- Maximum resilience against data quality issues
- Detailed error logs for debugging

---

## 3. Component Design

### 3.1 Product Fetch Background Job

**File:** `woo_prime/woo_prime/doctype/woo_item/woo_item.py`

#### 3.1.1 Entry Point Function (Updated)

```python
@frappe.whitelist()
def fetch_items_from_woocommerce(
	auto_create_missing=True,
	batch_size=10,
	skip_empty_products=False,
	background=True
):
	"""
	Enqueue product fetch as background job.
	
	Args:
		auto_create_missing: Auto-create ERPNext Items for unmatched products
		batch_size: Products per API page request (default 10)
		skip_empty_products: Skip products with empty name or SKU
		background: If True, enqueue to background; if False, run synchronously
	
	Returns:
		dict: Status and message
	"""
	# Parse parameters
	auto_create_missing = _parse_bool(auto_create_missing)
	skip_empty_products = _parse_bool(skip_empty_products)
	background = _parse_bool(background)
	batch_size = _parse_int(batch_size, default=10)
	
	if background:
		# Enqueue background job
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce_background",
			queue="long",
			timeout=7200,
			enqueue_after_commit=True,
			job_name=f"woo_fetch_products_{frappe.session.user}",
			auto_create_missing=auto_create_missing,
			batch_size=batch_size,
			skip_empty_products=skip_empty_products,
			user=frappe.session.user
		)
		
		msg = _(
			"Product fetch started in background ({0} items per page). "
			"You can monitor progress in real-time."
		).format(batch_size)
		
		if frappe.request:
			frappe.msgprint(msg, title=_("Fetch Queued"), indicator="blue")
		
		return {
			"status": "queued",
			"message": msg
		}
	else:
		# Run synchronously (for backward compatibility or testing)
		return fetch_items_from_woocommerce_background(
			auto_create_missing=auto_create_missing,
			batch_size=batch_size,
			skip_empty_products=skip_empty_products,
			user=frappe.session.user
		)
```

#### 3.1.2 Background Worker Function (New)

```python
def fetch_items_from_woocommerce_background(
	auto_create_missing=True,
	batch_size=10,
	skip_empty_products=False,
	user=None
):
	"""
	Background worker function for fetching products from WooCommerce.
	
	This function is executed by the background worker process.
	It performs pagination, per-page commits, per-product savepoints,
	and real-time progress updates.
	
	Args:
		auto_create_missing: Auto-create ERPNext Items for unmatched products
		batch_size: Products per API page request
		skip_empty_products: Skip products with empty name or SKU
		user: User who initiated the fetch (for realtime notifications)
	
	Returns:
		dict: Statistics (fetched, linked, created, failed counts)
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
	import json as _json
	
	# Initialize
	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None)
	if not default_item_group:
		default_item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	
	logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
	api = get_woo_api()
	
	# Counters
	page = 1
	total_fetched = 0
	auto_linked = 0
	created_erpnext_items = 0
	skipped_empty = 0
	failed_products = 0
	
	# Send initial progress
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
	
	try:
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
			page_skipped = 0
			page_failed = 0
			page_linked = 0
			page_created = 0
			
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
					
					# Log error
					logger.error(f"[FetchProducts] Product {woo_id} failed: {prod_error}")
					create_log(
						sync_type="Item",
						direction="Incoming",
						status="Failed",
						woo_reference_id=str(woo_id),
						error_message=str(prod_error)[:500]
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
		
		logger.info(f"[FetchProducts] Completed: {total_fetched} fetched, {auto_linked} linked, {created_erpnext_items} created, {skipped_empty} skipped, {failed_products} failed")
		
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
		logger.error(f"[FetchProducts] Fatal error: {e}\n{frappe.get_traceback()}")
		
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
```

#### 3.1.3 Helper Functions

```python
def _process_single_product(prod, settings, default_item_group, auto_create_missing, logger):
	"""
	Process a single product from WooCommerce response.
	
	Returns:
		dict: {"linked": bool, "created": bool}
	"""
	woo_id = prod.get("id")
	sku = (prod.get("sku") or "").strip()
	name = html.unescape(prod.get("name") or "")
	permalink = prod.get("permalink", "")
	description = html.unescape(prod.get("description", "") or "")
	short_description = html.unescape(prod.get("short_description", "") or "")
	
	if not sku:
		sku = f"WC-{woo_id}"
	
	# Find existing Woo Item
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
	
	# Auto-link to ERPNext Item
	matched_item = (
		frappe.db.get_value("Item", {"item_code": sku}, "name")
		or frappe.db.get_value("Item", {"name": sku}, "name")
		or frappe.db.get_value("Item", {"item_name": name}, "name")
	)
	
	linked = False
	created = False
	
	if matched_item:
		woo_item.item_code = matched_item
		woo_item.sync_status = "Synced"
		linked = True
	else:
		if auto_create_missing and not frappe.db.exists("Item", sku):
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
			created = True
			linked = True
		elif not woo_item.item_code:
			woo_item.sync_status = "Not Synced"
	
	woo_item.save(ignore_permissions=True)
	
	return {"linked": linked, "created": created}


def _publish_progress(user, operation, current_page, total_fetched, linked, created, skipped, failed, status, message):
	"""
	Publish real-time progress update.
	
	Args:
		user: Target user for notification
		operation: "fetch_products" or "fetch_categories"
		current_page: Current page number
		total_fetched: Total items fetched
		linked: Total items linked
		created: Total items created
		skipped: Total items skipped
		failed: Total items failed
		status: "starting", "fetching", "in_progress", "completed", "error"
		message: Human-readable status message
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


def _parse_bool(value):
	"""Parse various boolean representations."""
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.lower() in ("true", "1", "yes")
	return bool(value)


def _parse_int(value, default=10):
	"""Parse integer with default fallback."""
	try:
		return int(value)
	except (ValueError, TypeError):
		return default
```

### 3.2 Category Fetch Background Job

**File:** `woo_prime/woo_prime/doctype/woo_category/woo_category.py`

#### 3.2.1 Entry Point Function (Updated)

```python
@frappe.whitelist()
def sync_categories_from_woo(background=True):
	"""
	Enqueue category fetch as background job.
	
	Args:
		background: If True, enqueue to background; if False, run synchronously
	
	Returns:
		dict: Status and message
	"""
	background = _parse_bool(background)
	
	if background:
		# Enqueue background job
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo_background",
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
			job_name=f"woo_fetch_categories_{frappe.session.user}",
			user=frappe.session.user
		)
		
		msg = _("Category fetch started in background. You can monitor progress in real-time.")
		
		if frappe.request:
			frappe.msgprint(msg, title=_("Fetch Queued"), indicator="blue")
		
		return {
			"status": "queued",
			"message": msg
		}
	else:
		# Run synchronously
		return sync_categories_from_woo_background(user=frappe.session.user)


def _parse_bool(value):
	"""Parse various boolean representations."""
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.lower() in ("true", "1", "yes")
	return bool(value)
```

#### 3.2.2 Background Worker Function (New)

```python
def sync_categories_from_woo_background(user=None):
	"""
	Background worker function for fetching categories from WooCommerce.
	
	This function performs pagination, per-page commits, and real-time progress updates.
	
	Args:
		user: User who initiated the fetch (for realtime notifications)
	
	Returns:
		dict: Statistics (total_synced count)
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	
	logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
	
	# Send initial progress
	_publish_progress_category(
		user=user,
		current_page=0,
		total_synced=0,
		status="starting",
		message="Initializing category fetch..."
	)
	
	try:
		api = get_woo_api()
		page = 1
		total_synced = 0
		all_categories = []
		
		# --- Pass 1: Fetch all categories from WooCommerce ---
		logger.info("[FetchCategories] Starting pagination...")
		
		while True:
			_publish_progress_category(
				user=user,
				current_page=page,
				total_synced=total_synced,
				status="fetching",
				message=f"Fetching category page {page}..."
			)
			
			response = api.get("products/categories", params={"per_page": 100, "page": page})
			
			if response.status_code != 200:
				error_msg = _("Failed to fetch categories from WooCommerce: {0}").format(response.text[:300])
				logger.error(f"[FetchCategories] {error_msg}")
				
				_publish_progress_category(
					user=user,
					current_page=page,
					total_synced=total_synced,
					status="error",
					message=error_msg
				)
				
				frappe.throw(error_msg)
			
			categories = response.json()
			
			if not categories:
				break
			
			all_categories.extend(categories)
			logger.info(f"[FetchCategories] Page {page} — received {len(categories)} categories")
			
			# Commit page fetch
			frappe.db.commit()
			
			page += 1
		
		logger.info(f"[FetchCategories] Fetched {len(all_categories)} total categories")
		
		# --- Pass 2: Process categories ---
		_publish_progress_category(
			user=user,
			current_page=page,
			total_synced=0,
			status="processing",
			message=f"Processing {len(all_categories)} categories..."
		)
		
		# Build lookup: WooCommerce category ID → category data
		woo_id_to_data = {cat.get("id"): cat for cat in all_categories}
		
		# Determine parent categories
		parent_ids = set()
		for cat_data in all_categories:
			parent_woo_id = cat_data.get("parent")
			if parent_woo_id and parent_woo_id != 0:
				parent_ids.add(parent_woo_id)
		
		# Map: WooCommerce category ID → ERPNext Woo Category name
		woo_id_to_name = {}
		
		# --- Pass 2A: Create/Update basic category records & set is_group ---
		for idx, cat_data in enumerate(all_categories):
			cat_id = cat_data.get("id")
			cat_name = cat_data.get("name")
			slug = cat_data.get("slug")
			description = cat_data.get("description", "")
			
			if not cat_name:
				continue
			
			savepoint_name = f"category_{cat_id}"
			try:
				frappe.db.savepoint(savepoint_name)
				
				existing_name = (
					frappe.db.get_value("Woo Category", {"woo_category_id": cat_id})
					or frappe.db.get_value("Woo Category", {"category_name": cat_name})
				)
				
				if existing_name:
					cat_doc = frappe.get_doc("Woo Category", existing_name)
				else:
					cat_doc = frappe.new_doc("Woo Category")
					cat_doc.category_name = cat_name
				
				cat_doc.woo_category_id = cat_id
				cat_doc.slug = slug
				cat_doc.description = description
				cat_doc.is_group = 1 if cat_id in parent_ids else 0
				
				cat_doc.flags.ignore_mandatory = True
				cat_doc.save(ignore_permissions=True)
				woo_id_to_name[cat_id] = cat_doc.name
				
			except Exception as cat_error:
				frappe.db.rollback(savepoint_name)
				logger.error(f"[FetchCategories] Category {cat_id} failed: {cat_error}")
			
			# Periodic progress update
			if idx % 20 == 0:
				_publish_progress_category(
					user=user,
					current_page=page,
					total_synced=idx,
					status="processing",
					message=f"Processing categories ({idx}/{len(all_categories)})..."
				)
		
		frappe.db.commit()
		logger.info("[FetchCategories] Pass 2A complete — basic records created")
		
		# --- Pass 2B: Link parent categories (topologically sorted) ---
		_publish_progress_category(
			user=user,
			current_page=page,
			total_synced=len(all_categories),
			status="linking",
			message="Linking category hierarchy..."
		)
		
		parent_map = {cat.get("id"): (cat.get("parent") or 0) for cat in all_categories}
		
		def get_depth(cat_id):
			depth = 0
			curr = cat_id
			visited = set()
			while curr in parent_map and parent_map[curr] != 0:
				if curr in visited:
					break
				visited.add(curr)
				curr = parent_map[curr]
				depth += 1
			return depth
		
		sorted_categories = sorted(all_categories, key=lambda c: get_depth(c.get("id")))
		
		for cat_data in sorted_categories:
			cat_id = cat_data.get("id")
			cat_name = woo_id_to_name.get(cat_id)
			if not cat_name:
				continue
			
			parent_woo_id = cat_data.get("parent")
			cat_doc = frappe.get_doc("Woo Category", cat_name)
			
			if parent_woo_id and parent_woo_id != 0:
				parent_name = woo_id_to_name.get(parent_woo_id) or frappe.db.get_value("Woo Category", {"woo_category_id": parent_woo_id})
				if parent_name:
					cat_doc.parent_woo_category = parent_name
			else:
				cat_doc.parent_woo_category = None
			
			cat_doc.save(ignore_permissions=True)
			total_synced += 1
		
		frappe.db.commit()
		logger.info("[FetchCategories] Pass 2B complete — hierarchy linked")
		
		# --- Rebuild tree ---
		_publish_progress_category(
			user=user,
			current_page=page,
			total_synced=total_synced,
			status="rebuilding",
			message="Rebuilding category tree..."
		)
		
		rebuild_tree("Woo Category")
		frappe.db.commit()
		logger.info("[FetchCategories] Tree rebuild complete")
		
		# --- Send completion notification ---
		msg = _("✅ Successfully synced {0} product categories from WooCommerce!").format(total_synced)
		
		_publish_progress_category(
			user=user,
			current_page=page,
			total_synced=total_synced,
			status="completed",
			message=msg
		)
		
		logger.info(f"[FetchCategories] Completed: {total_synced} categories synced")
		
		return {
			"status": "success",
			"total_synced": total_synced
		}
		
	except Exception as e:
		logger.error(f"[FetchCategories] Fatal error: {e}\n{frappe.get_traceback()}")
		
		_publish_progress_category(
			user=user,
			current_page=0,
			total_synced=0,
			status="error",
			message=f"Fatal error: {str(e)[:200]}"
		)
		
		raise


def _publish_progress_category(user, current_page, total_synced, status, message):
	"""
	Publish real-time category fetch progress update.
	"""
	if not user:
		return
	
	try:
		frappe.publish_realtime(
			event="woo_fetch_progress",
			message={
				"operation": "fetch_categories",
				"current_page": current_page,
				"total_synced": total_synced,
				"status": status,
				"message": message,
				"timestamp": frappe.utils.now()
			},
			user=user,
			after_commit=False
		)
	except Exception as e:
		frappe.logger("woo_prime").error(f"Failed to publish category progress: {e}")
```

---

## 4. Configuration Changes

### 4.1 New Woo Settings Field: Skip Empty Products

Add a checkbox field to the Woo Settings DocType to control whether empty products should be skipped during fetch.

**File:** `woo_prime/woo_prime/doctype/woo_settings/woo_settings.json`

**Field Definition:**

```json
{
	"fieldname": "skip_empty_products",
	"fieldtype": "Check",
	"label": "Skip Empty Products",
	"description": "Skip products with empty name or SKU during fetch from WooCommerce",
	"default": "0",
	"insert_after": "fetch_items_btn"
}
```

**Field Order Update:**

Insert `skip_empty_products` after `fetch_items_btn` in the `field_order` array.

### 4.2 Woo Settings Python Hook (Optional)

No Python code changes needed in `woo_settings.py` for this field — it's purely read by the fetch function.

---

## 5. UI Changes

### 5.1 Woo Settings Form JavaScript

**File:** `woo_prime/woo_prime/doctype/woo_settings/woo_settings.js`

#### 5.1.1 Button Click Handlers (Updated)

```javascript
frappe.ui.form.on('Woo Settings', {
	fetch_items_btn: function(frm) {
		// Show dialog for fetch options
		let d = new frappe.ui.Dialog({
			title: __('Fetch Items from WooCommerce'),
			fields: [
				{
					fieldname: 'batch_size',
					fieldtype: 'Int',
					label: __('Products per Page'),
					default: 10,
					reqd: 1,
					description: __('Number of products to fetch per API request (1-100)')
				},
				{
					fieldname: 'auto_create_missing',
					fieldtype: 'Check',
					label: __('Auto-create ERPNext Items'),
					default: 1,
					description: __('Automatically create ERPNext Item records for unmatched products')
				},
				{
					fieldname: 'skip_empty_products',
					fieldtype: 'Check',
					label: __('Skip Empty Products'),
					default: frm.doc.skip_empty_products || 0,
					description: __('Skip products with empty name or SKU')
				}
			],
			primary_action_label: __('Start Fetch'),
			primary_action: function(values) {
				d.hide();
				
				// Show progress dialog
				show_fetch_progress_dialog('fetch_products');
				
				// Start background fetch
				frappe.call({
					method: 'woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce',
					args: {
						batch_size: values.batch_size,
						auto_create_missing: values.auto_create_missing,
						skip_empty_products: values.skip_empty_products,
						background: true
					},
					callback: function(r) {
						if (r.message && r.message.status === 'queued') {
							frappe.show_alert({
								message: __('Product fetch queued successfully'),
								indicator: 'blue'
							}, 5);
						}
					}
				});
			}
		});
		
		d.show();
	},
	
	fetch_categories_btn: function(frm) {
		frappe.confirm(
			__('Fetch all product categories from WooCommerce? This will run in the background.'),
			function() {
				// Show progress dialog
				show_fetch_progress_dialog('fetch_categories');
				
				// Start background fetch
				frappe.call({
					method: 'woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo',
					args: {
						background: true
					},
					callback: function(r) {
						if (r.message && r.message.status === 'queued') {
							frappe.show_alert({
								message: __('Category fetch queued successfully'),
								indicator: 'blue'
							}, 5);
						}
					}
				});
			}
		);
	}
});

// Progress dialog
let progress_dialog = null;
let progress_subscription = null;

function show_fetch_progress_dialog(operation) {
	// Close existing dialog if any
	if (progress_dialog) {
		progress_dialog.hide();
	}
	
	// Create progress dialog
	progress_dialog = new frappe.ui.Dialog({
		title: operation === 'fetch_products' ? __('Fetching Products') : __('Fetching Categories'),
		indicator: 'blue',
		size: 'large',
		fields: [
			{
				fieldname: 'progress_html',
				fieldtype: 'HTML'
			}
		],
		primary_action_label: __('Close'),
		primary_action: function() {
			progress_dialog.hide();
			if (progress_subscription) {
				frappe.realtime.off('woo_fetch_progress', progress_subscription);
				progress_subscription = null;
			}
		}
	});
	
	// Initialize progress HTML
	let html = `
		<div class="woo-fetch-progress">
			<div class="progress" style="height: 30px; margin-bottom: 20px;">
				<div class="progress-bar progress-bar-striped progress-bar-animated" 
					role="progressbar" 
					style="width: 0%;" 
					id="woo-progress-bar">
				</div>
			</div>
			<div class="row" style="margin-bottom: 15px;">
				<div class="col-sm-12">
					<p id="woo-status-message" style="font-size: 14px; color: #555;">
						<i class="fa fa-spinner fa-spin"></i> Initializing...
					</p>
				</div>
			</div>
			<div class="row" id="woo-stats-row" style="display: none;">
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-fetched">0</h4>
						<p class="text-muted" style="margin: 0;">Fetched</p>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-linked">0</h4>
						<p class="text-muted" style="margin: 0;">Linked</p>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-created">0</h4>
						<p class="text-muted" style="margin: 0;">Created</p>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="well well-sm text-center">
						<h4 id="woo-stat-failed">0</h4>
						<p class="text-muted" style="margin: 0;">Failed</p>
					</div>
				</div>
			</div>
		</div>
	`;
	
	progress_dialog.fields_dict.progress_html.$wrapper.html(html);
	progress_dialog.show();
	
	// Subscribe to realtime updates
	progress_subscription = function(data) {
		if (data.operation !== operation) {
			return;
		}
		
		update_progress_dialog(data);
		
		// Auto-close on completion or error after 5 seconds
		if (data.status === 'completed' || data.status === 'error') {
			setTimeout(function() {
				if (progress_dialog) {
					progress_dialog.hide();
				}
			}, 5000);
		}
	};
	
	frappe.realtime.on('woo_fetch_progress', progress_subscription);
}

function update_progress_dialog(data) {
	if (!progress_dialog) {
		return;
	}
	
	// Update status message
	let status_icon = 'fa-spinner fa-spin';
	let status_color = '#555';
	
	if (data.status === 'completed') {
		status_icon = 'fa-check-circle';
		status_color = '#28a745';
	} else if (data.status === 'error') {
		status_icon = 'fa-exclamation-circle';
		status_color = '#dc3545';
	}
	
	$('#woo-status-message').html(
		`<i class="fa ${status_icon}" style="color: ${status_color};"></i> ${data.message}`
	);
	
	// Update progress bar (estimate based on page number if not provided)
	if (data.status === 'completed') {
		$('#woo-progress-bar').css('width', '100%').removeClass('progress-bar-animated');
	} else if (data.status === 'error') {
		$('#woo-progress-bar').removeClass('progress-bar-striped progress-bar-animated').addClass('bg-danger');
	}
	
	// Update stats
	if (data.operation === 'fetch_products') {
		$('#woo-stats-row').show();
		$('#woo-stat-fetched').text(data.total_fetched || 0);
		$('#woo-stat-linked').text(data.linked || 0);
		$('#woo-stat-created').text(data.created || 0);
		$('#woo-stat-failed').text(data.failed || 0);
	} else if (data.operation === 'fetch_categories') {
		// Simplified stats for categories
		$('#woo-stats-row').hide();
	}
}
```

---

## 6. Error Handling Strategy

### 6.1 Error Classification

| Error Type | Handling Strategy | Recovery Action |
|-----------|-------------------|-----------------|
| API Connection Error | Abort operation, log error | User retry, check credentials |
| API Rate Limit (429) | Exponential backoff, retry | Auto-retry with delay |
| Individual Product Error | Rollback savepoint, log, continue | Process remaining products |
| Page Commit Error | Rollback page, log, break | Last successful page persisted |
| Fatal Python Error | Log traceback, notify user | Manual investigation required |

### 6.2 Logging Strategy

**File-based Logging:**
```python
logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
logger.info(f"[FetchProducts] Page {page} started")
logger.error(f"[FetchProducts] Product {prod_id} failed: {error}")
```

**Database Logging (Woo Sync Log):**
```python
create_log(
	sync_type="Item",
	direction="Incoming",
	status="Failed",
	woo_reference_id=str(woo_id),
	error_message=str(error)[:500],
	request_data=request_json,
	response_data=response_json
)
```

**User Notifications:**
- Real-time progress via `frappe.publish_realtime`
- Final msgprint on completion
- Error alerts for fatal failures

---

## 7. Testing Strategy

### 7.1 Unit Tests

**Test File:** `woo_prime/woo_prime/doctype/woo_item/test_woo_item.py`

```python
def test_fetch_products_background_single_page():
	"""Test background fetch with single page of products."""
	# Mock WooCommerce API response
	# Call fetch_items_from_woocommerce_background
	# Assert product count, linked count, created count

def test_fetch_products_with_savepoint_recovery():
	"""Test that individual product errors don't fail the page."""
	# Mock API with one invalid product
	# Verify other products are still processed

def test_skip_empty_products():
	"""Test skip_empty_products flag."""
	# Mock API with empty-name products
	# Verify they are skipped when flag is enabled
```

### 7.2 Integration Tests

- Test with real WooCommerce sandbox environment
- Verify per-page commits with database inspection
- Test progress notifications with manual verification
- Test concurrent job prevention with duplicate enqueue attempts

### 7.3 Performance Tests

- Fetch 1000+ products with pagination
- Monitor memory usage during fetch
- Verify commit frequency and database lock duration
- Benchmark savepoint overhead

---

## 8. Migration & Deployment

### 8.1 Backward Compatibility

The updated functions maintain backward compatibility:
- `background=False` parameter allows synchronous execution (default changed to `True`)
- Existing API calls continue to work
- No database schema changes required (except new Woo Settings field)

### 8.2 Deployment Checklist

1. Add `skip_empty_products` field to Woo Settings DocType
2. Deploy updated Python code for `woo_item.py` and `woo_category.py`
3. Deploy updated JavaScript for `woo_settings.js`
4. Clear cache: `bench clear-cache`
5. Restart workers: `bench restart`
6. Test fetch operations in staging environment
7. Monitor logs and Woo Sync Log during first production fetch

---

## 9. Monitoring & Observability

### 9.1 Log Files

- **Application Log:** `logs/woo_prime.log` (paginated, max 1MB per file, 50 files)
- **Error Log:** `logs/woo_prime.error.log` (Frappe auto-generated)

### 9.2 Database Monitoring

**Woo Sync Log DocType:**
- Query by `sync_type="Item"` and `direction="Incoming"` to see fetch history
- Filter by `status="Failed"` to see errors
- Check `request_data` and `response_data` for debugging

### 9.3 Real-time Monitoring

Users can monitor fetch progress in real-time through the progress dialog, which displays:
- Current page being processed
- Total items fetched/linked/created/skipped/failed
- Status messages for each phase
- Error messages for failures

---

## 10. Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Per-Page Commit Atomicity

*For any* page of products/categories fetched from WooCommerce, if the page processing completes without exceptions, then all products/categories from that page SHALL be committed to the database before processing the next page.

**Validates: Requirements 1.1, 1.2**

### Property 2: Savepoint Isolation

*For any* individual product within a page, if that product's processing fails, then the failure SHALL NOT affect the processing or database persistence of other products in the same page.

**Validates: Requirements 1.3**

### Property 3: Progress Notification Consistency

*For any* background fetch operation, the real-time progress notifications sent to the user SHALL accurately reflect the current state of the operation (current page, items processed, items failed) at the time of notification.

**Validates: Requirements 1.4**

### Property 4: Empty Product Filtering

*For any* product fetched from WooCommerce, if `skip_empty_products` is enabled AND the product has an empty name OR empty SKU, then the product SHALL be skipped and excluded from the total fetched count.

**Validates: Requirements 1.5**

### Property 5: Job Deduplication

*For any* user attempting to start a fetch operation while a fetch operation of the same type is already running for that user, the new fetch request SHALL be rejected and the existing job SHALL continue running.

**Validates: Requirements 1.6**

### Property 6: Matching Behavior Preservation

*For any* product fetched from WooCommerce, the automatic linking to ERPNext Items SHALL use the exact same matching logic (SKU, item_code, item_name) as the current synchronous implementation.

**Validates: Requirements 1.7**

### Property 7: Category Hierarchy Integrity

*For any* category tree fetched from WooCommerce, after the sync operation completes, the parent-child relationships in ERPNext SHALL exactly match the parent-child relationships in WooCommerce, and the NestedSet lft/rgt values SHALL be consistent.

**Validates: Requirements 1.8**

---

## 11. Security Considerations

### 11.1 Authentication

- Background jobs run with system privileges (`ignore_permissions=True`)
- User context is preserved via `user` parameter for audit trails
- API credentials are read from Woo Settings (secured by Frappe permissions)

### 11.2 Input Validation

- All user-provided parameters are validated and sanitized
- HTML content from WooCommerce is unescaped using `html.unescape()`
- SQL injection protected by Frappe ORM

### 11.3 Resource Limits

- Job timeout: 7200 seconds (2 hours)
- Queue: `long` (isolated from default queue)
- Per-page commits prevent unbounded memory growth

---

## 12. Performance Considerations

### 12.1 Database Performance

- **Per-page commits:** Reduce transaction size and lock duration
- **Savepoints:** Minimal overhead (~0.1ms per savepoint)
- **Batch size:** Configurable (default 10), allows tuning for site performance
- **Index usage:** Existing indexes on `woo_product_id`, `sku`, `item_code` are utilized

### 12.2 Network Performance

- **Pagination:** Prevents large response payloads
- **Persistent sessions:** `WooAPI` uses `requests.Session()` for connection pooling
- **Timeout:** 30-second per-request timeout prevents hanging

### 12.3 Worker Resource Usage

- **Memory:** Bounded by batch size and commit frequency
- **CPU:** Minimal (mostly I/O bound)
- **Concurrency:** One long-queue worker can handle one fetch operation at a time

---

## 13. Future Enhancements

### 13.1 Potential Improvements

- **Resume from last page:** Store progress in database, allow resume on failure
- **Incremental sync:** Fetch only products modified since last sync using `modified_after` parameter
- **Parallel processing:** Process multiple pages concurrently (requires careful transaction management)
- **Rate limit handling:** Auto-detect 429 responses and implement exponential backoff
- **Webhook-based sync:** Eliminate need for full fetch by processing WooCommerce product.created/product.updated webhooks

### 13.2 Not Included in This Design

These enhancements are out of scope for the current design but may be considered in future iterations:
- Background jobs for stock sync and price sync (separate features)
- Advanced retry logic with exponential backoff
- Product image download and caching
- Variant-specific fetch optimizations

---

## 14. Code Style & Conventions

All code follows the project standards:
- **Python version:** >= 3.14
- **Indentation:** Tabs (not spaces)
- **Line length:** 110 characters (ruff configuration)
- **Imports:** Grouped (stdlib, third-party, frappe, local)
- **Docstrings:** Google style with Args and Returns sections
- **Logging:** Structured with operation prefix (e.g., `[FetchProducts]`)
- **Error messages:** User-friendly with technical details in logs

---

## 15. Appendix: API Reference

### 15.1 Python Functions

#### `fetch_items_from_woocommerce(auto_create_missing, batch_size, skip_empty_products, background)`

Entry point for product fetch. Enqueues background job or runs synchronously.

**Parameters:**
- `auto_create_missing` (bool): Auto-create ERPNext Items for unmatched products
- `batch_size` (int): Products per API page request
- `skip_empty_products` (bool): Skip products with empty name or SKU
- `background` (bool): If True, enqueue; if False, run synchronously

**Returns:** `dict` with status and message

#### `fetch_items_from_woocommerce_background(auto_create_missing, batch_size, skip_empty_products, user)`

Background worker for product fetch.

**Parameters:**
- `auto_create_missing` (bool)
- `batch_size` (int)
- `skip_empty_products` (bool)
- `user` (str): User who initiated fetch

**Returns:** `dict` with statistics

#### `sync_categories_from_woo(background)`

Entry point for category fetch.

**Parameters:**
- `background` (bool): If True, enqueue; if False, run synchronously

**Returns:** `dict` with status and message

#### `sync_categories_from_woo_background(user)`

Background worker for category fetch.

**Parameters:**
- `user` (str): User who initiated fetch

**Returns:** `dict` with statistics

### 15.2 Realtime Events

#### `woo_fetch_progress`

Published by background workers, subscribed by UI.

**Message Payload:**
```python
{
	"operation": "fetch_products" | "fetch_categories",
	"current_page": int,
	"total_fetched": int,
	"linked": int,
	"created": int,
	"skipped": int,
	"failed": int,
	"status": "starting" | "fetching" | "in_progress" | "completed" | "error",
	"message": str,
	"timestamp": str
}
```

---

## 16. Glossary

- **Background Job:** Asynchronous task executed by Frappe worker process
- **Savepoint:** Database transaction marker allowing partial rollback
- **Per-page Commit:** Database commit after processing each page of API results
- **Realtime Pub/Sub:** Frappe's WebSocket-based event system for live updates
- **Long Queue:** Frappe job queue for long-running operations (>5 minutes)
- **Woo Item:** ERPNext DocType representing a WooCommerce product
- **Woo Category:** ERPNext DocType representing a WooCommerce product category
- **Auto-link:** Automatic matching of Woo Items to ERPNext Items by SKU
- **NestedSet:** Frappe's tree structure implementation using lft/rgt values

---

**End of Design Document**
