# Implementation Plan: Background Job Conversion for WooCommerce Fetch Operations

## Overview

Convert synchronous WooCommerce product and category fetch operations to background jobs with robust error handling, per-page commits, per-product savepoints, realtime progress notifications, and enhanced logging. The implementation preserves existing matching logic and duplicate prevention while adding resilience against browser timeouts and partial failures.

## Tasks

- [x] 1. Set up helper functions and utilities
	- [x] 1.1 Create helper functions for background job infrastructure
		- Implement `_parse_bool(value)` function to parse boolean parameters (string "true"/"1"/"yes" → bool)
		- Implement `_parse_int(value, default)` function to parse integer with fallback
		- Implement `_publish_progress()` function for realtime progress notifications (products)
		- Implement `_publish_progress_category()` function for realtime progress notifications (categories)
		- Add comprehensive docstrings for each helper function
		- _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

- [ ] 2. Implement product fetch background job infrastructure
	- [x] 2.1 Update `fetch_items_from_woocommerce()` entry point function
		- Modify function signature to include `skip_empty_products` parameter (default `False`)
		- Parse `background` parameter with string support ("true"/"1" → True)
		- Parse `auto_create_missing`, `batch_size`, and `skip_empty_products` parameters
		- Implement background job enqueue with `queue="long"`, `timeout=7200`, `enqueue_after_commit=True`
		- Set `job_name=f"woo_fetch_products_{frappe.session.user}"` for job deduplication
		- Return immediate response with status "queued" when `background=True`
		- Display msgprint with indicator "blue" and title "Fetch Queued"
		- Keep backward compatibility: run synchronously when `background=False`
		- _Requirements: 1.1, 1.4, 11.1, 11.2, 11.3_
	
	- [x] 2.2 Create `fetch_items_from_woocommerce_background()` worker function
		- Create new function with signature: `(auto_create_missing, batch_size, skip_empty_products, user)`
		- Initialize Frappe logger with site-specific logging: `frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)`
		- Load Woo Settings and get default Item Group
		- Initialize counters: `total_fetched`, `auto_linked`, `created_erpnext_items`, `skipped_empty`, `failed_products`
		- Send initial progress notification with status "starting"
		- Implement main pagination loop with `page` counter
		- Add comprehensive error logging at all stages
		- _Requirements: 1.1, 1.4, 5.1, 5.2, 5.7_
	
	- [x] 2.3 Implement per-page API request and response handling
		- Build request params with `per_page=batch_size` and `page=page`
		- Log request with page number and params at "info" level
		- Send progress notification with status "fetching"
		- Execute API GET request: `api.get("products", params=req_params)`
		- Build request log JSON with method, endpoint, params, page
		- Build response log JSON with status_code, headers, body_preview (first 5000 chars)
		- Handle non-200 status codes: log error, create failed Woo Sync Log, break pagination
		- Parse JSON response and check for empty array (end of pagination)
		- _Requirements: 1.1, 5.2, 5.3, 5.6, 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_
	
	- [x] 2.4 Implement per-product processing with savepoints
		- Loop through products in current page
		- Extract product data: `woo_id`, `sku`, `name`, `permalink`, `description`, `short_description`
		- Apply HTML unescaping: `html.unescape()` for name and descriptions
		- Check skip_empty_products flag: skip if empty name OR empty SKU
		- Generate Fake_SKU `WC-{woo_id}` if sku is empty (and not skipping)
		- Create savepoint before processing: `frappe.db.savepoint(f"product_{woo_id}")`
		- Call `_process_single_product()` helper (to be implemented)
		- Update counters based on result (linked, created)
		- On exception: rollback savepoint, log error, create Individual_Error_Log, increment failed_count
		- _Requirements: 1.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 8.1, 8.2, 8.7, 12.1, 12.2, 12.3, 12.4, 12.5_
	
	- [x] 2.5 Create `_process_single_product()` helper function
		- Accept parameters: `prod`, `settings`, `default_item_group`, `auto_create_missing`, `logger`
		- Find existing Woo Item by: `woo_product_id`, then `sku`, then primary key name
		- Create new Woo Item if not found
		- Set Woo Item fields: `woo_product_id`, `sku`, `woo_product_url`, descriptions, `published=1`
		- Set prices: `regular_price`, `sale_price` (using `frappe.utils.flt()`)
		- Auto-link to ERPNext Item by matching: `item_code`, then `name`, then `item_name`
		- If matched: set `item_code`, set `sync_status="Synced"`, return `{"linked": True, "created": False}`
		- If not matched and `auto_create_missing=True`: create new Item with `item_code=sku`, `item_name=name`, `item_group`, `stock_uom="Nos"`, `is_stock_item=1`
		- After creating Item: set `item_code`, set `sync_status="Synced"`, return `{"linked": True, "created": True}`
		- If not matched and not auto-creating: set `sync_status="Not Synced"`, return `{"linked": False, "created": False}`
		- Save Woo Item with `ignore_permissions=True`
		- _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 10.1, 10.2, 10.3_
	
	- [-] 2.6 Implement per-page commit and progress notifications
		- After processing all products in page, call `frappe.db.commit()`
		- Wrap commit in try/except: on failure, log error, rollback, send error notification, break pagination
		- Log commit success with page number and counters
		- Send progress notification with status "in_progress" and page summary message
		- Increment page counter
		- _Requirements: 3.1, 3.2, 3.3, 3.4, 5.4_
	
	- [x] 2.7 Implement final completion and error handling
		- After pagination loop completes, build final summary message
		- Include in message: total_fetched, auto_linked, created_erpnext_items, skipped_empty, failed_products
		- Send final progress notification with status "completed"
		- Log final summary at "info" level with elapsed time
		- Return statistics dictionary: `{"status": "success", "fetched": ..., "linked": ..., "created": ..., "skipped": ..., "failed": ...}`
		- Wrap entire worker function in try/except: on fatal error, log traceback, send error notification, re-raise
		- _Requirements: 5.5, 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7_
	
	- [x] 2.8 Add success logging for product page fetches
		- After successful API response and JSON parse, create Woo Sync Log entry
		- Set `sync_type="Item"`, `direction="Incoming"`, `status="Success"`
		- Include `request_data` with JSON serialized request params
		- Include `response_data` with status code, headers, body preview (5000 chars)
		- Log once per page, not per product
		- _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

- [x] 3. Implement category fetch background job infrastructure
	- [x] 3.1 Update `sync_categories_from_woo()` entry point function
		- Add `background` parameter to function signature (default `True`)
		- Parse `background` parameter with string support
		- Implement background job enqueue with `queue="long"`, `timeout=3600`, `enqueue_after_commit=True`
		- Set `job_name=f"woo_fetch_categories_{frappe.session.user}"` for job deduplication
		- Return immediate response with status "queued" when `background=True`
		- Display msgprint with indicator "blue" and title "Category Sync Queued"
		- Keep backward compatibility: call `sync_categories_from_woo_background()` when `background=False`
		- _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_
	
	- [x] 3.2 Create `sync_categories_from_woo_background()` worker function
		- Create new function with signature: `(user=None)`
		- Initialize Frappe logger with site-specific logging
		- Send initial progress notification with status "starting"
		- Initialize counters: `page`, `total_synced`, `all_categories=[]`
		- Implement Pass 1: Fetch all categories with pagination (100 per page)
		- Send progress notification before each page fetch with status "fetching"
		- Handle API errors: log, send error notification, raise exception
		- Extend `all_categories` list with each page's results
		- Commit after fetching each page
		- Log total categories fetched after Pass 1 completes
		- _Requirements: 2.1, 2.5, 6.1, 6.2, 6.3, 6.4_
	
	- [x] 3.3 Implement Pass 2A: Create/update category records with savepoints
		- Send progress notification with status "processing"
		- Build lookup: `woo_id_to_data = {cat.get("id"): cat for cat in all_categories}`
		- Identify parent categories: `parent_ids = set(cat.get("parent") for cat in all_categories if cat.get("parent"))`
		- Initialize `woo_id_to_name = {}` mapping
		- Loop through `all_categories` with index
		- For each category: create savepoint `f"category_{cat_id}"`
		- Find existing Woo Category by `woo_category_id` or `category_name`
		- Create new or update existing: set `woo_category_id`, `slug`, `description`
		- Set `is_group=1` if `cat_id in parent_ids`, else `is_group=0`
		- Save with `ignore_permissions=True` and `ignore_mandatory=True`
		- Store mapping: `woo_id_to_name[cat_id] = cat_doc.name`
		- On exception: rollback savepoint, log error
		- Send periodic progress updates every 20 categories
		- Commit after Pass 2A completes
		- _Requirements: 6.5, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 9.1, 9.2, 10.4, 10.5_
	
	- [x] 3.4 Implement Pass 2B: Link parent-child relationships
		- Send progress notification with status "linking"
		- Build parent map: `parent_map = {cat.get("id"): cat.get("parent") or 0 for cat in all_categories}`
		- Implement `get_depth(cat_id)` helper function (counts levels to root with cycle detection)
		- Topologically sort categories: `sorted_categories = sorted(all_categories, key=lambda c: get_depth(c.get("id")))`
		- Loop through sorted categories
		- For each: get category doc, check for parent_woo_id
		- If has parent: find parent name from `woo_id_to_name` or database
		- Set `parent_woo_category` field (or None for root)
		- Save category doc with `ignore_permissions=True`
		- Increment `total_synced` counter
		- Commit after Pass 2B completes
		- _Requirements: 6.6, 9.3, 9.4_
	
	- [x] 3.5 Implement tree rebuild and completion
		- Send progress notification with status "rebuilding"
		- Call `rebuild_tree("Woo Category")` to fix lft/rgt values
		- Commit after tree rebuild
		- Log completion at "info" level
		- Build final summary message with `total_synced` count
		- Send final progress notification with status "completed"
		- Return statistics dictionary: `{"status": "success", "total_synced": total_synced}`
		- Wrap entire worker in try/except: on fatal error, log traceback, send error notification, re-raise
		- _Requirements: 6.7, 6.8, 9.5, 9.6_
	
	- [x] 3.6 Add success logging for category page fetches
		- After successful API response for each category page, create Woo Sync Log entry
		- Set `sync_type="Category"`, `direction="Incoming"`, `status="Success"`
		- Include `request_data` with JSON serialized request params
		- Include `response_data` with status code and body preview
		- Log once per page fetch
		- _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

- [x] 4. Add skip_empty_products configuration field to Woo Settings
	- [x] 4.1 Update Woo Settings DocType JSON schema
		- Open `woo_prime/woo_prime/doctype/woo_settings/woo_settings.json`
		- Add new field definition in `fields` array after `fetch_items_btn`
		- Field properties: `"fieldname": "skip_empty_products"`, `"fieldtype": "Check"`, `"label": "Skip Empty Products"`, `"description": "Skip products with empty name or SKU during fetch from WooCommerce"`, `"default": "0"`
		- Update `field_order` array to include `"skip_empty_products"` after `"fetch_items_btn"`
		- Save and commit JSON file
		- _Requirements: 12.1, 12.4_

- [x] 5. Update UI JavaScript for Woo Settings form
	- [x] 5.1 Implement product fetch button with dialog
		- Open `woo_prime/woo_prime/doctype/woo_settings/woo_settings.js`
		- Update `fetch_items_btn` event handler
		- Create `frappe.ui.Dialog` with fields: `batch_size` (Int, default 10), `auto_create_missing` (Check, default 1), `skip_empty_products` (Check, default from form)
		- Add field descriptions for each option
		- Set `primary_action_label: __('Start Fetch')`
		- In primary_action: call `show_fetch_progress_dialog('fetch_products')`, then `frappe.call()` to `fetch_items_from_woocommerce` with `background=true`
		- Show alert on success: "Product fetch queued successfully" with blue indicator
		- _Requirements: 1.1, 1.3, 12.4_
	
	- [x] 5.2 Implement category fetch button with confirmation
		- Update `fetch_categories_btn` event handler (if exists, otherwise create)
		- Use `frappe.confirm()` to ask user confirmation: "Fetch all product categories from WooCommerce? This will run in the background."
		- On confirm: call `show_fetch_progress_dialog('fetch_categories')`, then `frappe.call()` to `sync_categories_from_woo` with `background=true`
		- Show alert on success: "Category fetch queued successfully" with blue indicator
		- _Requirements: 2.1, 2.3_
	
	- [x] 5.3 Implement progress dialog UI component
		- Create `show_fetch_progress_dialog(operation)` function
		- Close existing dialog if open
		- Create `frappe.ui.Dialog` with title based on operation ("Fetching Products" or "Fetching Categories")
		- Add HTML field with progress bar, status message, and stats counters (Fetched, Linked, Created, Failed)
		- Style progress bar with Bootstrap classes: `progress-bar-striped`, `progress-bar-animated`
		- Add close button as primary action
		- Initialize progress HTML with 0% bar and "Initializing..." message
		- Show dialog immediately
		- _Requirements: 1.4_
	
	- [x] 5.4 Implement realtime progress subscription
		- In `show_fetch_progress_dialog()`, subscribe to `frappe.realtime.on('woo_fetch_progress', handler)`
		- Filter events by `data.operation` to match current operation
		- Call `update_progress_dialog(data)` on each event
		- Auto-close dialog 5 seconds after completion or error status
		- Store subscription reference for cleanup: `progress_subscription`
		- Unsubscribe on dialog close: `frappe.realtime.off('woo_fetch_progress', progress_subscription)`
		- _Requirements: 1.4_
	
	- [x] 5.5 Implement progress dialog update logic
		- Create `update_progress_dialog(data)` function
		- Update status message with icon based on status: spinner (in_progress), check (completed), exclamation (error)
		- Update status message color: gray (in_progress), green (completed), red (error)
		- Update progress bar width to 100% on completion, remove animation
		- Set progress bar to red background on error
		- Update stats counters: `#woo-stat-fetched`, `#woo-stat-linked`, `#woo-stat-created`, `#woo-stat-failed`
		- Show stats row for product fetch, hide for category fetch
		- _Requirements: 1.4_

- [x] 6. Add comprehensive error logging
	- [x] 6.1 Implement Individual_Error_Log creation for product failures
		- In `fetch_items_from_woocommerce_background()`, when product processing fails
		- Create Woo Sync Log entry with `sync_type="Item"`, `direction="Incoming"`, `status="Failed"`
		- Set `woo_reference_id=str(woo_id)`
		- Set `error_message` to full exception traceback (first 500 chars)
		- Include SKU in reference information (can use custom field or message)
		- Commit immediately after creating log
		- _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_
	
	- [x] 6.2 Implement Individual_Error_Log creation for category failures
		- In `sync_categories_from_woo_background()`, when category processing fails (Pass 2A or 2B)
		- Create Woo Sync Log entry with `sync_type="Category"`, `direction="Incoming"`, `status="Failed"`
		- Set `woo_reference_id=str(cat_id)`
		- Set `error_message` to full exception traceback
		- Include category name in reference information
		- Commit immediately after creating log
		- _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [~] 7. Checkpoint - Test background job execution
	- Verify product fetch enqueues to long queue and executes asynchronously
	- Verify category fetch enqueues to long queue and executes asynchronously
	- Test backward compatibility: `background=False` runs synchronously
	- Test job deduplication: concurrent requests queue sequentially
	- Verify per-page commits save partial progress
	- Verify savepoints isolate individual product/category failures
	- Check Woo Sync Log entries for success and failure cases
	- Monitor application logs for detailed progress entries
	- Test UI progress dialog displays and updates correctly
	- Ensure all tests pass, ask the user if questions arise

- [ ] 8. Test skip_empty_products functionality
	- [~] 8.1 Test skipping products with empty name
		- Create test with mock WooCommerce API response containing product with empty name
		- Call `fetch_items_from_woocommerce_background()` with `skip_empty_products=True`
		- Verify product is skipped and `skipped_empty` counter incremented
		- Verify skip is logged at "info" level
		- _Requirements: 12.1, 12.5_
	
	- [~] 8.2 Test skipping products with empty SKU
		- Create test with mock response containing product with empty SKU
		- Call worker function with `skip_empty_products=True`
		- Verify product is skipped
		- Verify log entry includes product ID and reason
		- _Requirements: 12.1, 12.5_
	
	- [~] 8.3 Test processing empty products when flag is disabled
		- Create test with empty-name product and `skip_empty_products=False`
		- Verify product is processed and Fake_SKU is generated
		- Verify Woo Item is created/updated
		- _Requirements: 8.7, 12.4_

- [ ] 9. Verify backward compatibility and existing behavior preservation
	- [~] 9.1 Test synchronous execution mode
		- Call `fetch_items_from_woocommerce(background=False)` directly
		- Verify it executes synchronously (no enqueue)
		- Verify return value contains `{"fetched": ..., "linked": ..., "created": ...}`
		- Verify msgprint displays with green indicator and "Fetch Complete" title
		- _Requirements: 11.1, 11.2, 11.3_
	
	- [~] 9.2 Test string parameter parsing
		- Call entry points with `background="true"`, `background="1"`, `background="false"`, `background="0"`
		- Verify correct boolean interpretation
		- Test `auto_create_missing="true"` and other string values
		- _Requirements: 11.2_
	
	- [~] 9.3 Test duplicate prevention for products
		- Run fetch twice for same products
		- Verify Woo Items are updated, not duplicated
		- Verify ERPNext Items are not duplicated
		- Check by `woo_product_id`, `sku`, and `item_code` matching
		- _Requirements: 10.1, 10.2, 10.3_
	
	- [~] 9.4 Test duplicate prevention for categories
		- Run category sync twice
		- Verify Woo Categories are updated, not duplicated
		- Check by `woo_category_id` and `category_name` matching
		- Verify tree structure remains correct
		- _Requirements: 10.4, 10.5_
	
	- [~] 9.5 Test matching logic preservation for products
		- Create ERPNext Items with specific `item_code` and `item_name`
		- Run product fetch
		- Verify auto-linking uses correct precedence: `item_code` match, then `name` match, then `item_name` match
		- Verify HTML unescaping works for names and descriptions
		- Verify Fake_SKU generation for products without SKU
		- _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_
	
	- [~] 9.6 Test category hierarchy preservation
		- Fetch categories with multi-level parent-child relationships
		- Verify `is_group` is set correctly for parent categories
		- Verify `parent_woo_category` links are correct
		- Verify topological sort processes parents before children
		- Verify `rebuild_tree()` produces correct lft/rgt values
		- _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [ ] 10. Integration testing with realistic data
	- [~] 10.1 Test large product fetch (1000+ products)
		- Use test WooCommerce instance with large product catalog
		- Monitor memory usage during fetch
		- Verify per-page commits occur every 100 products (or batch_size)
		- Check log files for all progress milestones
		- Verify final statistics are accurate
		- _Requirements: 1.1, 3.1, 3.4, 5.4, 5.5_
	
	- [~] 10.2 Test category fetch with deep hierarchy
		- Use test instance with nested categories (5+ levels deep)
		- Verify topological sort handles cycles gracefully
		- Verify all parent-child links are correct
		- Verify tree rebuild completes successfully
		- _Requirements: 9.3, 9.4, 9.5, 9.6_
	
	- [~] 10.3 Test error resilience
		- Inject errors in individual products (invalid data)
		- Verify other products in same page are processed successfully
		- Verify Individual_Error_Log entries are created
		- Verify failed_products counter is accurate
		- Verify page commit succeeds despite individual failures
		- _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_
	
	- [~] 10.4 Test API error handling
		- Simulate API errors: 401 (auth), 429 (rate limit), 500 (server error), 503 (unavailable)
		- Verify 4xx errors stop pagination and raise exception
		- Verify 5xx errors on first page raise exception
		- Verify 5xx errors on subsequent pages log and stop gracefully
		- Verify Woo Sync Log captures error details
		- _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_
	
	- [~] 10.5 Test realtime notifications in browser
		- Open Woo Settings form in browser
		- Click "Fetch Items" button with progress dialog
		- Verify progress bar updates during fetch
		- Verify counters update in real-time
		- Verify status messages change (fetching → processing → completed)
		- Verify dialog auto-closes after completion
		- Repeat for "Fetch Categories" button
		- _Requirements: 1.4_

- [~] 11. Final checkpoint - Ensure all tests pass and requirements are met
	- Run full test suite for product fetch background jobs
	- Run full test suite for category fetch background jobs
	- Verify all 16 requirements from requirements.md are satisfied
	- Verify all 7 correctness properties from design.md hold
	- Review code for Python >= 3.14 compatibility
	- Verify tab indentation and ruff line-length 110 compliance
	- Review all docstrings and comments for clarity
	- Check for any TODOs or FIXMEs in code
	- Ensure all tests pass, ask the user if questions arise

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- All code follows project standards: Python >= 3.14, tab indentation, ruff line-length 110
- Background jobs use Frappe's `long` queue with 2-hour timeout for products, 1-hour for categories
- Per-page commits ensure partial progress is saved on failure
- Per-product/per-category savepoints ensure individual errors don't fail entire pages
- Realtime notifications provide live progress updates to users
- Backward compatibility is maintained: `background=False` allows synchronous execution
- Existing matching logic and duplicate prevention are preserved exactly

## Task Dependency Graph

```json
{
	"waves": [
		{ "id": 0, "tasks": ["1.1", "4.1"] },
		{ "id": 1, "tasks": ["2.1", "3.1"] },
		{ "id": 2, "tasks": ["2.2", "2.5"] },
		{ "id": 3, "tasks": ["2.3", "3.2"] },
		{ "id": 4, "tasks": ["2.4", "3.3"] },
		{ "id": 5, "tasks": ["2.6", "3.4"] },
		{ "id": 6, "tasks": ["2.7", "2.8", "3.5", "3.6"] },
		{ "id": 7, "tasks": ["5.1", "5.2"] },
		{ "id": 8, "tasks": ["5.3", "5.4"] },
		{ "id": 9, "tasks": ["5.5", "6.1", "6.2"] },
		{ "id": 10, "tasks": ["8.1", "8.2", "8.3"] },
		{ "id": 11, "tasks": ["9.1", "9.2", "9.3", "9.4"] },
		{ "id": 12, "tasks": ["9.5", "9.6", "10.1", "10.2"] },
		{ "id": 13, "tasks": ["10.3", "10.4", "10.5"] }
	]
}
```
