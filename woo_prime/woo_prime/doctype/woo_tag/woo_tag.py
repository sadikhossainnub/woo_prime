# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WooTag(Document):
	def validate(self):
		if not self.slug and self.tag_name:
			self.slug = frappe.scrub(self.tag_name).replace("_", "-")


@frappe.whitelist()
def fetch_tags_from_woo(background=False):
	"""Fetch all product tags from WooCommerce and sync to Woo Tag DocType."""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	api = get_woo_api()
	page = 1
	total_tags = 0

	try:
		while True:
			res = api.get("products/tags", params={"per_page": 100, "page": page})
			if res.status_code != 200:
				frappe.throw(_("Failed to fetch tags from WooCommerce: {0}").format(res.text[:200]))

			tags = res.json()
			if not tags or not isinstance(tags, list):
				break

			for tag in tags:
				tag_id = int(tag.get("id"))
				tag_name = (tag.get("name") or "").strip()
				if not tag_name:
					continue

				slug = tag.get("slug") or frappe.scrub(tag_name).replace("_", "-")
				description = tag.get("description", "")
				count = int(tag.get("count", 0))

				existing_woo_tag = (
					frappe.db.get_value("Woo Tag", {"woo_tag_id": tag_id})
					or (frappe.db.exists("Woo Tag", tag_name) and tag_name)
				)

				if existing_woo_tag:
					woo_tag = frappe.get_doc("Woo Tag", existing_woo_tag)
				else:
					woo_tag = frappe.new_doc("Woo Tag")
					woo_tag.tag_name = tag_name

				woo_tag.woo_tag_id = tag_id
				woo_tag.slug = slug
				woo_tag.description = description
				woo_tag.count = count
				woo_tag.save(ignore_permissions=True)
				total_tags += 1

			page += 1

		frappe.db.commit()

		msg = _("Successfully fetched {0} tag(s) from WooCommerce!").format(total_tags)
		if frappe.request:
			frappe.msgprint(msg, title=_("Tags Synced"), indicator="green")

		return {"tags_synced": total_tags, "message": msg}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="woo_prime: Fetch Tags Failed", message=frappe.get_traceback())
		frappe.throw(_("Tag fetch failed: {0}").format(str(e)))

