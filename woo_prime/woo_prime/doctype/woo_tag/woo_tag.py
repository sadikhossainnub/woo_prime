# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from woo_prime.utils import repair_crashed_tables, safe_log_error, clean_text


class WooTag(Document):
	def validate(self):
		if not self.slug and self.tag_name:
			self.slug = frappe.scrub(self.tag_name).replace("_", "-")


def _publish_progress(user, operation, status, message, **kwargs):
	"""Publish real-time progress update."""
	if not user:
		return
	try:
		frappe.publish_realtime(
			event="woo_fetch_progress",
			message={
				"operation": operation,
				"status": status,
				"message": message,
				"timestamp": frappe.utils.now(),
				**kwargs,
			},
			user=user,
			after_commit=False,
		)
	except Exception as e:
		frappe.logger("woo_prime").error(f"Failed to publish progress: {e}")


def ensure_woo_tag_table():
	"""Ensure tabWoo Tag table exists in MariaDB."""
	try:
		if not frappe.db.table_exists("Woo Tag"):
			frappe.reload_doc("woo_prime", "doctype", "woo_tag")
	except Exception:
		pass

	try:
		if not frappe.db.table_exists("Woo Tag"):
			frappe.db.sql("""
				CREATE TABLE IF NOT EXISTS `tabWoo Tag` (
					`name` varchar(140) NOT NULL PRIMARY KEY,
					`creation` datetime(6) DEFAULT NULL,
					`modified` datetime(6) DEFAULT NULL,
					`modified_by` varchar(140) DEFAULT NULL,
					`owner` varchar(140) DEFAULT NULL,
					`docstatus` int(1) NOT NULL DEFAULT 0,
					`idx` int(8) NOT NULL DEFAULT 0,
					`tag_name` varchar(140) NOT NULL,
					`woo_tag_id` bigint(20) DEFAULT NULL,
					`slug` varchar(140) DEFAULT NULL,
					`count` int(11) DEFAULT 0,
					`description` text DEFAULT NULL,
					INDEX `woo_tag_id` (`woo_tag_id`),
					INDEX `tag_name` (`tag_name`),
					INDEX `slug` (`slug`)
				) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
			""")
			frappe.db.commit()
	except Exception as e:
		frappe.logger("woo_prime").error(f"Error creating tabWoo Tag table: {e}")


def _save_single_tag(tag):
	"""Save or update a single tag into Woo Tag and ERPNext standard Tag."""
	tag_id = int(tag.get("id")) if tag.get("id") else 0
	tag_name = clean_text(tag.get("name"))
	if not tag_name:
		return False

	slug = tag.get("slug") or frappe.scrub(tag_name).replace("_", "-")
	description = clean_text(tag.get("description", ""))
	count = int(tag.get("count", 0))

	# 1. Ensure tag exists in ERPNext standard Tag DocType
	try:
		if not frappe.db.exists("Tag", tag_name):
			t_doc = frappe.new_doc("Tag")
			t_doc.name = tag_name
			t_doc.description = description
			t_doc.flags.ignore_permissions = True
			t_doc.insert(ignore_permissions=True)
	except Exception:
		pass

	# 2. Save or update Woo Tag
	try:
		existing_woo_tag = None
		if tag_id:
			existing_woo_tag = frappe.db.get_value("Woo Tag", {"woo_tag_id": tag_id}, "name")
		if not existing_woo_tag:
			existing_woo_tag = frappe.db.get_value("Woo Tag", {"tag_name": tag_name}, "name")
		if not existing_woo_tag and frappe.db.exists("Woo Tag", tag_name):
			existing_woo_tag = tag_name

		if existing_woo_tag:
			woo_tag = frappe.get_doc("Woo Tag", existing_woo_tag)
			if tag_id:
				woo_tag.woo_tag_id = tag_id
			woo_tag.slug = slug
			woo_tag.description = description
			woo_tag.count = count
			woo_tag.flags.ignore_permissions = True
			woo_tag.flags.ignore_mandatory = True
			woo_tag.save(ignore_permissions=True)
		else:
			woo_tag = frappe.new_doc("Woo Tag")
			woo_tag.tag_name = tag_name
			if tag_id:
				woo_tag.woo_tag_id = tag_id
			woo_tag.slug = slug
			woo_tag.description = description
			woo_tag.count = count
			woo_tag.flags.ignore_permissions = True
			woo_tag.flags.ignore_mandatory = True
			woo_tag.insert(ignore_permissions=True)
		return True
	except Exception as e:
		frappe.logger("woo_prime").error(f"Failed to save Woo Tag '{tag_name}': {e}")
		return False


@frappe.whitelist()
def fetch_tags_from_woo(background=True):
	"""Enqueue tag fetch as background job or run synchronously."""
	from woo_prime.woo_prime.doctype.woo_category.woo_category import _parse_bool

	background = _parse_bool(background)

	if background:
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_tag.woo_tag._fetch_tags_worker",
			queue="long",
			timeout=1800,
			enqueue_after_commit=True,
			job_name=f"woo_fetch_tags_{frappe.session.user}",
			user=frappe.session.user,
		)

		msg = _("Tag fetch started in background. You can monitor progress in real-time.")
		if frappe.request:
			frappe.msgprint(msg, title=_("Tag Sync Queued"), indicator="blue")

		return {"status": "queued", "message": msg}
	else:
		return _fetch_tags_worker(user=frappe.session.user)


def _fetch_tags_worker(user=None):
	"""Background worker: Fetch all product tags from WooCommerce with fallbacks."""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	repair_crashed_tables()
	ensure_woo_tag_table()

	api = get_woo_api()
	operation = "fetch_tags"
	page = 1
	total_tags = 0

	_publish_progress(user, operation, "starting", "Initializing tag fetch...")

	try:
		# Step 1: Query WooCommerce products/tags endpoint (with hide_empty=False)
		while True:
			_publish_progress(
				user, operation, "fetching",
				f"Fetching tags page {page}...",
				total_synced=total_tags,
			)

			res = api.get("products/tags", params={"per_page": 100, "page": page, "hide_empty": False}, timeout=60)
			if res.status_code != 200:
				# Fallback if hide_empty parameter isn't supported
				res = api.get("products/tags", params={"per_page": 100, "page": page}, timeout=60)

			if res.status_code != 200:
				break

			tags = res.json()
			if not tags or not isinstance(tags, list):
				break

			for tag in tags:
				if _save_single_tag(tag):
					total_tags += 1

			if len(tags) < 100:
				page += 1
				frappe.db.commit()
				break

			page += 1
			frappe.db.commit()

		# Step 2: Fallback - if 0 tags were found from products/tags, fetch tags embedded in products
		if total_tags == 0:
			_publish_progress(
				user, operation, "fetching",
				"Scanning WooCommerce products for tags...",
				total_synced=0,
			)
			prod_page = 1
			seen_tags = {}
			while prod_page <= 10:  # Check up to 1,000 products
				p_res = api.get("products", params={"per_page": 100, "page": prod_page, "_fields": "id,tags"}, timeout=60)
				if p_res.status_code != 200:
					break
				prods = p_res.json()
				if not prods or not isinstance(prods, list):
					break
				for p in prods:
					for t in p.get("tags", []):
						t_name = clean_text(t.get("name"))
						if t_name and t_name not in seen_tags:
							seen_tags[t_name] = t
							if _save_single_tag(t):
								total_tags += 1
				if len(prods) < 100:
					break
				prod_page += 1
				frappe.db.commit()

		# Step 3: Fallback - check wp/v2/tags if still 0
		if total_tags == 0:
			try:
				wp_res = api.get("../wp/v2/tags", params={"per_page": 100}, timeout=45)
				if wp_res.status_code == 200 and isinstance(wp_res.json(), list):
					for t in wp_res.json():
						if _save_single_tag(t):
							total_tags += 1
					frappe.db.commit()
			except Exception:
				pass

		_publish_progress(
			user, operation, "completed",
			f"✅ Successfully fetched {total_tags} tag(s)!",
			total_synced=total_tags,
		)

		msg = _("Successfully fetched {0} tag(s) from WooCommerce!").format(total_tags)
		return {"status": "success", "tags_synced": total_tags, "message": msg}

	except Exception as e:
		frappe.db.rollback()
		safe_log_error(title="woo_prime: Fetch Tags Failed", message=frappe.get_traceback())
		_publish_progress(user, operation, "error", f"❌ Tag fetch failed: {str(e)}")
		frappe.throw(_("Tag fetch failed: {0}").format(str(e)))
