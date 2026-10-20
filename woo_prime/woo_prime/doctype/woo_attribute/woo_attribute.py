# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import html
import re
import frappe
from frappe import _
from frappe.model.document import Document
from woo_prime.utils import repair_crashed_tables, safe_log_error, clean_text, generate_unique_abbr


class WooAttribute(Document):
	def validate(self):
		if not self.slug and self.attribute_name:
			self.slug = frappe.scrub(self.attribute_name).replace("_", "-")


def _publish_progress(user, operation, status, message, **kwargs):
	"""Publish real-time progress update for sync operations."""
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


@frappe.whitelist()
def fetch_attributes_from_woo(background=True):
	"""Enqueue attribute fetch as background job or run synchronously."""
	frappe.only_for(["System Manager", "Woo Manager"])
	from woo_prime.woo_prime.doctype.woo_category.woo_category import _parse_bool

	background = _parse_bool(background)

	if background:
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_attribute.woo_attribute._fetch_attributes_worker",
			queue="long",
			timeout=1800,
			enqueue_after_commit=True,
			job_name=f"woo_fetch_attributes_{frappe.session.user}",
			user=frappe.session.user,
		)

		msg = _("Attribute fetch started in background. You can monitor progress in real-time.")
		if frappe.request:
			frappe.msgprint(msg, title=_("Attribute Sync Queued"), indicator="blue")

		return {"status": "queued", "message": msg}
	else:
		return _fetch_attributes_worker(user=frappe.session.user)


def _fetch_attributes_worker(user=None):
	"""Background worker: Fetch all product attributes and terms from WooCommerce."""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	repair_crashed_tables()

	api = get_woo_api()
	logger = frappe.logger("woo_prime", allow_site=True)
	operation = "fetch_attributes"

	page = 1
	total_attrs = 0
	total_terms = 0

	_publish_progress(user, operation, "starting", "Initializing attribute fetch...")

	try:
		while True:
			_publish_progress(
				user, operation, "fetching",
				f"Fetching attributes page {page}...",
				total_synced=total_attrs,
			)

			res = api.get("products/attributes", params={"per_page": 50, "page": page}, timeout=60)
			if res.status_code != 200:
				error_msg = _("Failed to fetch attributes from WooCommerce: {0}").format(res.text[:200])
				_publish_progress(user, operation, "error", error_msg)
				frappe.throw(error_msg)

			attributes = res.json()
			if not attributes or not isinstance(attributes, list):
				break

			for idx, attr in enumerate(attributes):
				attr_id = int(attr.get("id"))
				attr_name = clean_text(attr.get("name"))
				if not attr_name:
					continue

				_publish_progress(
					user, operation, "processing",
					f"Processing attribute: {attr_name} ({total_attrs + 1})...",
					total_synced=total_attrs,
				)

				slug = attr.get("slug") or frappe.scrub(attr_name).replace("_", "-")
				attr_type = attr.get("type", "select")
				order_by = attr.get("order_by", "menu_order")
				has_archives = 1 if attr.get("has_archives") else 0

				# 1. Upsert Woo Attribute DocType
				existing_woo_attr = (
					frappe.db.get_value("Woo Attribute", {"woo_attribute_id": attr_id})
					or (frappe.db.exists("Woo Attribute", attr_name) and attr_name)
				)

				if existing_woo_attr:
					woo_attr = frappe.get_doc("Woo Attribute", existing_woo_attr)
				else:
					woo_attr = frappe.new_doc("Woo Attribute")
					woo_attr.attribute_name = attr_name

				woo_attr.woo_attribute_id = attr_id
				woo_attr.slug = slug
				woo_attr.type = attr_type
				woo_attr.order_by = order_by
				woo_attr.has_archives = has_archives

				# 2. Fetch terms for this attribute (paginated, resilient to timeouts)
				terms = []
				term_page = 1
				while True:
					try:
						terms_res = api.get(
							f"products/attributes/{attr_id}/terms",
							params={"per_page": 50, "page": term_page},
							timeout=45,
						)
						if terms_res.status_code != 200:
							break
						batch = terms_res.json()
						if not batch or not isinstance(batch, list):
							break
						terms.extend(batch)
						if len(batch) < 50:
							break
						term_page += 1
					except Exception as te:
						logger.warning(f"Could not fetch terms page {term_page} for attribute {attr_name} (ID: {attr_id}): {te}")
						break

				# 3. Upsert ERPNext Item Attribute & Values
				try:
					if frappe.db.exists("Item Attribute", attr_name):
						item_attr_doc = frappe.get_doc("Item Attribute", attr_name)
					else:
						item_attr_doc = frappe.new_doc("Item Attribute")
						item_attr_doc.attribute_name = attr_name
						item_attr_doc.item_attribute_values = []

					existing_abbrs = set()
					existing_values = set()

					for row in (item_attr_doc.item_attribute_values or []):
						row_val = clean_text(row.attribute_value)
						row.attribute_value = row_val
						existing_values.add(row_val.lower())
						row_abbr = (row.abbr or "").strip()
						if not row_abbr or row_abbr.lower() in existing_abbrs:
							row_abbr = generate_unique_abbr(row_val, existing_abbrs)
							row.abbr = row_abbr
						else:
							existing_abbrs.add(row_abbr.lower())

					for term in terms:
						t_name = clean_text(term.get("name"))
						if not t_name:
							continue
						total_terms += 1
						if t_name.lower() not in existing_values:
							new_abbr = generate_unique_abbr(t_name, existing_abbrs)
							item_attr_doc.append("item_attribute_values", {
								"attribute_value": t_name,
								"abbr": new_abbr,
							})
							existing_values.add(t_name.lower())

					item_attr_doc.flags.ignore_permissions = True
					item_attr_doc.save(ignore_permissions=True)
					woo_attr.erpnext_item_attribute = item_attr_doc.name
				except Exception as e:
					logger.error(f"Failed to sync Item Attribute {attr_name}: {e}")

				woo_attr.save(ignore_permissions=True)
				total_attrs += 1
				frappe.db.commit()

			page += 1

		_publish_progress(
			user, operation, "completed",
			f"✅ Successfully fetched {total_attrs} attribute(s) and {total_terms} term(s)!",
			total_synced=total_attrs,
			total_terms=total_terms,
		)

		msg = _("Successfully fetched {0} attribute(s) and {1} term(s) from WooCommerce!").format(total_attrs, total_terms)
		return {"status": "success", "attributes_synced": total_attrs, "terms_synced": total_terms, "message": msg}

	except Exception as e:
		frappe.db.rollback()
		safe_log_error(title="woo_prime: Fetch Attributes Failed", message=frappe.get_traceback())
		_publish_progress(user, operation, "error", f"❌ Attribute fetch failed: {str(e)}")
		frappe.throw(_("Attribute fetch failed: {0}").format(str(e)))


def ensure_woo_attribute_tables():
	"""Ensure tabWoo Attribute and tabWoo Attribute Mapping tables exist in MariaDB."""
	for dt in ("woo_attribute", "woo_attribute_mapping"):
		try:
			if not frappe.db.table_exists(dt.replace("_", " ").title()):
				frappe.reload_doc("woo_prime", "doctype", dt)
		except Exception:
			pass


@frappe.whitelist()
def push_attribute_to_woo(attribute_name):
	"""Push a single ERPNext Item Attribute and its values to WooCommerce as a Global Attribute."""
	frappe.only_for(["System Manager", "Woo Manager"])
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	repair_crashed_tables()
	ensure_woo_attribute_tables()

	if not frappe.db.exists("Item Attribute", attribute_name):
		frappe.throw(_("Item Attribute '{0}' not found in ERPNext.").format(attribute_name))

	attr_doc = frappe.get_doc("Item Attribute", attribute_name)
	api = get_woo_api()

	clean_name = clean_text(attr_doc.attribute_name or attribute_name)
	slug = frappe.scrub(clean_name).replace("_", "-")

	# 1. Check if attribute already exists on WooCommerce
	attr_id = None
	try:
		res = api.get("products/attributes")
		if res.status_code == 200 and isinstance(res.json(), list):
			for a in res.json():
				a_name = clean_text(a.get("name") or "").lower()
				a_slug = (a.get("slug") or "").lower().replace("pa_", "")
				if a_name == clean_name.lower() or a_slug == slug.lower():
					attr_id = int(a.get("id"))
					slug = a.get("slug") or slug
					break
	except Exception as e:
		frappe.logger("woo_prime").error(f"Error querying WC attributes: {e}")

	# 2. If not found on WooCommerce, create it
	if not attr_id:
		payload = {
			"name": clean_name,
			"slug": slug,
			"type": "select",
			"order_by": "menu_order",
			"has_archives": True,
		}
		create_res = api.post("products/attributes", data=payload)
		if create_res.status_code not in (200, 201):
			frappe.throw(_("Failed to create attribute on WooCommerce: {0}").format(create_res.text[:250]))
		data = create_res.json()
		attr_id = int(data.get("id"))
		slug = data.get("slug") or slug

	# 3. Push terms (values) from item_attribute_values
	existing_terms = set()
	try:
		t_page = 1
		while True:
			t_res = api.get(f"products/attributes/{attr_id}/terms", params={"per_page": 100, "page": t_page}, timeout=45)
			if t_res.status_code != 200:
				break
			batch = t_res.json()
			if not batch or not isinstance(batch, list):
				break
			for t in batch:
				existing_terms.add(clean_text(t.get("name")).lower())
			if len(batch) < 100:
				break
			t_page += 1
	except Exception:
		pass

	terms_pushed = 0
	for row in (attr_doc.item_attribute_values or []):
		val_name = clean_text(row.attribute_value)
		if not val_name or val_name.lower() in existing_terms:
			continue

		val_slug = frappe.scrub(val_name).replace("_", "-")
		try:
			term_res = api.post(
				f"products/attributes/{attr_id}/terms",
				data={"name": val_name, "slug": val_slug},
				timeout=45,
			)
			if term_res.status_code in (200, 201):
				terms_pushed += 1
				existing_terms.add(val_name.lower())
		except Exception as te:
			frappe.logger("woo_prime").error(f"Failed to push term '{val_name}' for attribute '{clean_name}': {te}")

	# 4. Save/update local Woo Attribute DocType
	woo_attr_name = frappe.db.get_value("Woo Attribute", {"woo_attribute_id": attr_id}, "name")
	if not woo_attr_name:
		woo_attr_name = frappe.db.get_value("Woo Attribute", {"attribute_name": clean_name}, "name")

	if woo_attr_name:
		wa = frappe.get_doc("Woo Attribute", woo_attr_name)
	else:
		wa = frappe.new_doc("Woo Attribute")
		wa.attribute_name = clean_name

	wa.woo_attribute_id = attr_id
	wa.slug = slug
	wa.type = "select"
	wa.erpnext_item_attribute = attr_doc.name
	wa.flags.ignore_permissions = True
	wa.save(ignore_permissions=True)

	# 5. Ensure mapped in Woo Settings
	try:
		settings = frappe.get_single("Woo Settings")
		existing_maps = {row.erpnext_attribute for row in (settings.attribute_mappings or [])}
		if attr_doc.name not in existing_maps:
			settings.append("attribute_mappings", {
				"erpnext_attribute": attr_doc.name,
				"woo_attribute": wa.name,
				"woo_attribute_id": attr_id,
				"woo_attribute_slug": slug,
			})
			settings.flags.ignore_permissions = True
			settings.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		pass

	msg = _("✅ Successfully pushed attribute <b>{0}</b> (ID: {1}) with {2} new term(s) to WooCommerce!").format(
		clean_name, attr_id, terms_pushed
	)
	if frappe.request:
		frappe.msgprint(msg, title=_("Attribute Pushed"), indicator="green")

	return {
		"status": "success",
		"woo_attribute_id": attr_id,
		"terms_pushed": terms_pushed,
		"message": msg,
	}


@frappe.whitelist()
def push_all_attributes_to_woo(background=True):
	"""Push all ERPNext Item Attributes to WooCommerce."""
	frappe.only_for(["System Manager", "Woo Manager"])
	from woo_prime.woo_prime.doctype.woo_category.woo_category import _parse_bool

	background = _parse_bool(background)

	if background:
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_attribute.woo_attribute._push_all_attributes_worker",
			queue="long",
			timeout=1800,
			enqueue_after_commit=True,
			job_name=f"woo_push_attributes_{frappe.session.user}",
			user=frappe.session.user,
		)

		msg = _("Attribute push started in background. You can monitor progress in real-time.")
		if frappe.request:
			frappe.msgprint(msg, title=_("Attribute Push Queued"), indicator="blue")

		return {"status": "queued", "message": msg}
	else:
		return _push_all_attributes_worker(user=frappe.session.user)


def _push_all_attributes_worker(user=None):
	"""Background worker: Push all ERPNext Item Attributes to WooCommerce."""
	operation = "push_attributes"
	all_attrs = frappe.get_all("Item Attribute", pluck="name")
	total = len(all_attrs)
	pushed_count = 0
	total_terms = 0

	_publish_progress(user, operation, "starting", f"Pushing {total} attribute(s) to WooCommerce...")

	for idx, attr_name in enumerate(all_attrs):
		_publish_progress(
			user, operation, "processing",
			f"Pushing attribute '{attr_name}' ({idx + 1}/{total})...",
			total_synced=pushed_count,
		)
		try:
			res = push_attribute_to_woo(attr_name)
			if res.get("status") == "success":
				pushed_count += 1
				total_terms += res.get("terms_pushed", 0)
		except Exception as e:
			frappe.logger("woo_prime").error(f"Error pushing attribute {attr_name}: {e}")

	_publish_progress(
		user, operation, "completed",
		f"✅ Successfully pushed {pushed_count}/{total} attribute(s) to WooCommerce!",
		total_synced=pushed_count,
		total_terms=total_terms,
	)

	msg = _("Successfully pushed {0} attribute(s) and {1} term(s) to WooCommerce!").format(pushed_count, total_terms)
	return {"status": "success", "attributes_pushed": pushed_count, "terms_pushed": total_terms, "message": msg}

