# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet, rebuild_tree


class WooCategory(NestedSet):
	nsm_parent_field = "parent_woo_category"

	def validate(self):
		if not self.slug and self.category_name:
			self.slug = frappe.scrub(self.category_name).replace("_", "-")

	def on_update(self):
		super().on_update()

	def after_rename(self, old_name, new_name, merge=False):
		super().after_rename(old_name, new_name, merge)


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

def _publish_progress_category(user, current_page, total_synced, status, message):
	"""Publish real-time progress update for category fetch operations.
	
	Sends a realtime notification via Frappe's publish_realtime to update
	the user interface with current progress of the category sync background job.
	The notification is sent immediately (after_commit=False) to provide
	responsive feedback during the multi-pass sync process.
	
	Args:
		user (str): Target user to receive the notification
		current_page (int): Current page number being processed during fetch phase
		total_synced (int): Total number of categories synced so far
		status (str): Current status ("starting", "fetching", "processing", "linking", 
		             "rebuilding", "completed", "error")
		message (str): Human-readable status message for display
	
	Returns:
		None
	
	Note:
		Exceptions during publish are caught and logged to prevent
		disruption of the main background job execution. This function
		is specific to category operations and includes fewer counters
		than the product version.
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


@frappe.whitelist()
def push_category_to_woo(doc_name):
	"""Push a single Woo Category to WooCommerce (create or update)."""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	doc = frappe.get_doc("Woo Category", doc_name)
	api = get_woo_api()

	payload = {
		"name": doc.category_name,
		"description": doc.description or "",
	}
	if doc.slug:
		payload["slug"] = doc.slug

	# Handle parent category
	if doc.parent_woo_category:
		parent_doc = frappe.get_doc("Woo Category", doc.parent_woo_category)
		if not parent_doc.woo_category_id:
			# Push parent first if it doesn't have a Woo ID
			push_category_to_woo(parent_doc.name)
			parent_doc.reload()
		if parent_doc.woo_category_id:
			payload["parent"] = int(parent_doc.woo_category_id)

	if doc.woo_category_id:
		# Update existing category on WooCommerce
		res = api.put(f"products/categories/{doc.woo_category_id}", data=payload)
		if res.status_code == 200:
			res_data = res.json()
			doc.slug = res_data.get("slug", doc.slug)
			doc.save(ignore_permissions=True)
			frappe.msgprint(
				_("✅ Updated Category '{0}' on WooCommerce!").format(doc.category_name),
				indicator="green",
				alert=True,
			)
			return res_data
		else:
			frappe.throw(_("Failed to update category on WooCommerce ({0}): {1}").format(res.status_code, res.text[:300]))
	else:
		# Create new category on WooCommerce
		res = api.post("products/categories", data=payload)
		if res.status_code in (200, 201):
			res_data = res.json()
			doc.woo_category_id = res_data.get("id")
			doc.slug = res_data.get("slug", doc.slug)
			doc.save(ignore_permissions=True)
			frappe.msgprint(
				_("✅ Created Category '{0}' on WooCommerce (ID: {1})!").format(doc.category_name, doc.woo_category_id),
				indicator="green",
				alert=True,
			)
			return res_data
		else:
			frappe.throw(_("Failed to create category on WooCommerce ({0}): {1}").format(res.status_code, res.text[:300]))


@frappe.whitelist()
def sync_categories_from_woo(background=True):
	"""Enqueue category fetch as background job or run synchronously.
	
	This entry point function handles both background and synchronous execution
	modes for category synchronization from WooCommerce to ERPNext.
	
	Args:
		background (bool or str): If True (default), enqueue to background job queue.
		                         If False, run synchronously. Supports string parsing
		                         ("true"/"1"/"yes" → True, others → False).
	
	Returns:
		dict: Status dictionary containing:
		      - background=True: {"status": "queued", "message": str}
		      - background=False: {"status": "success", "total_synced": int}
	
	Background Job Configuration:
		- Queue: "long" (suitable for operations >5 minutes)
		- Timeout: 3600 seconds (1 hour)
		- Job Name: "woo_fetch_categories_{user}" for deduplication
		- Enqueue After Commit: True (ensures transaction safety)
	
	User Notifications:
		- Background mode: Blue msgprint "Category Sync Queued"
		- Synchronous mode: Green msgprint "Categories Synced" (in worker function)
	
	Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
	"""
	# Parse background parameter (supports string values)
	background = _parse_bool(background)
	
	if background:
		# Enqueue background job to long queue
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo_background",
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
			job_name=f"woo_fetch_categories_{frappe.session.user}",
			user=frappe.session.user
		)
		
		msg = _("Category fetch started in background. You can monitor progress in real-time.")
		
		# Display msgprint only if in web request context
		if frappe.request:
			frappe.msgprint(msg, title=_("Category Sync Queued"), indicator="blue")
		
		return {
			"status": "queued",
			"message": msg
		}
	else:
		# Run synchronously (backward compatibility mode)
		return sync_categories_from_woo_background(user=frappe.session.user)


def sync_categories_from_woo_background(user=None):
	"""Background worker function for fetching categories from WooCommerce.
	
	This function executes asynchronously in the background worker process
	when enqueued via sync_categories_from_woo(background=True). It performs
	the actual category synchronization using a two-pass algorithm:
	
	Pass 1: Fetch all categories from WooCommerce (paginated, 100 per page)
	Pass 2A: Create/update category records and set is_group flags
	Pass 2B: Link parent-child relationships (topologically sorted)
	Final: Rebuild nested set tree structure
	
	Args:
		user (str): User who initiated the fetch (for realtime notifications)
	
	Returns:
		dict: Statistics dictionary with keys:
		      - status (str): "success"
		      - total_synced (int): Total number of categories synced
	
	Progress Notifications:
		Sends realtime progress updates via frappe.publish_realtime with
		event "woo_fetch_progress" at each major phase.
	
	Error Handling:
		- API errors: Logged and raised with error notification
		- Individual category errors: Logged with savepoint rollback, processing continues
		- Fatal errors: Logged with full traceback, error notification sent
	
	Database Transactions:
		- Commits after each page fetch (Pass 1)
		- Commits after Pass 2A (basic records)
		- Commits after Pass 2B (parent linking)
		- Commits after tree rebuild
	
	Requirements: 2.1, 2.5, 6.1-6.8, 9.1-9.6, 14.1-14.5
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	
	# Check if within sync window (silently skip if outside)
	if not _is_within_sync_window():
		return {
			"status": "skipped",
			"message": "Outside auto sync window",
			"total_synced": 0
		}
	
	logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
	
	# Send initial progress notification
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
				# Rollback to savepoint
				frappe.db.rollback(savepoint_name)
				
				# Log error with full traceback
				error_traceback = frappe.get_traceback()
				logger.error(f"[FetchCategories] Category {cat_id} ({cat_name}) failed: {cat_error}\n{error_traceback}")
				
				# Create Individual_Error_Log entry
				from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
				try:
					create_log(
						sync_type="Category",
						direction="Incoming",
						status="Failed",
						woo_reference_id=str(cat_id),
						error_message=f"Category: {cat_name}\n{error_traceback}"[:500]
					)
					# Commit error log immediately
					frappe.db.commit()
				except Exception as log_error:
					logger.error(f"[FetchCategories] Failed to create error log for category {cat_id}: {log_error}")
			
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
		
		# Display msgprint only if in web request context
		if frappe.request:
			frappe.msgprint(
				msg,
				title=_("Categories Synced"),
				indicator="green",
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


@frappe.whitelist()
def get_children(doctype, parent=None, is_root=False, **filters):
	"""Return child categories for tree view."""
	if is_root or is_root == "true":
		# Get root categories (no parent)
		cond_filters = [
			["parent_woo_category", "is", "not set"]
		]
	else:
		# Get children of specific parent
		cond_filters = {"parent_woo_category": parent}

	categories = frappe.get_all(
		"Woo Category",
		filters=cond_filters,
		fields=[
			"name as value",
			"category_name",
			"woo_category_id",
			"is_group as expandable",
			"parent_woo_category",
		],
		order_by="category_name asc",
	)

	return categories


@frappe.whitelist()
def add_node():
	"""Add a new category node from tree view and push it to WooCommerce."""
	args = frappe.form_dict
	category_name = args.get("category_name")
	parent = args.get("parent")
	is_root = args.get("is_root")

	if not category_name:
		frappe.throw(_("Category Name is required"))

	cat = frappe.new_doc("Woo Category")
	cat.category_name = category_name

	if not is_root or is_root == "false":
		if parent:
			parent_doc = frappe.get_doc("Woo Category", parent)
			if not parent_doc.is_group:
				parent_doc.is_group = 1
				parent_doc.save(ignore_permissions=True)
		cat.parent_woo_category = parent

	cat.is_group = 1 if args.get("is_group") else 0
	cat.save(ignore_permissions=True)

	# Automatically push new category to WooCommerce if integration is enabled
	try:
		push_category_to_woo(cat.name)
		cat.reload()
	except Exception as e:
		frappe.log_error(title="WooCommerce Category Push Error", message=str(e))

	return cat
