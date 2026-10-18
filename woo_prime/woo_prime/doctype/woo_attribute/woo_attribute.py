# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import json
import frappe
from frappe import _
from frappe.model.document import Document


class WooAttribute(Document):
	def validate(self):
		if not self.slug and self.attribute_name:
			self.slug = frappe.scrub(self.attribute_name).replace("_", "-")


@frappe.whitelist()
def fetch_attributes_from_woo(background=False):
	"""Fetch all product attributes and their terms from WooCommerce, sync to Woo Attribute and ERPNext Item Attribute."""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	api = get_woo_api()
	logger = frappe.logger("woo_prime", allow_site=True)

	page = 1
	total_attrs = 0
	total_terms = 0

	try:
		while True:
			res = api.get("products/attributes", params={"per_page": 100, "page": page})
			if res.status_code != 200:
				frappe.throw(_("Failed to fetch attributes from WooCommerce: {0}").format(res.text[:200]))

			attributes = res.json()
			if not attributes or not isinstance(attributes, list):
				break

			for attr in attributes:
				attr_id = int(attr.get("id"))
				attr_name = (attr.get("name") or "").strip()
				if not attr_name:
					continue

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

				# 2. Fetch terms for this attribute from WooCommerce
				terms_res = api.get(f"products/attributes/{attr_id}/terms", params={"per_page": 100})
				terms = terms_res.json() if terms_res.status_code == 200 and isinstance(terms_res.json(), list) else []

				# 3. Upsert ERPNext standard Item Attribute & Item Attribute Values
				item_attr_doc = None
				try:
					if frappe.db.exists("Item Attribute", attr_name):
						item_attr_doc = frappe.get_doc("Item Attribute", attr_name)
					else:
						item_attr_doc = frappe.new_doc("Item Attribute")
						item_attr_doc.attribute_name = attr_name
						item_attr_doc.item_attribute_values = []

					existing_values = {row.attribute_value.strip().lower() for row in (item_attr_doc.item_attribute_values or []) if row.attribute_value}

					for term in terms:
						t_name = (term.get("name") or "").strip()
						if not t_name:
							continue
						total_terms += 1

						if t_name.lower() not in existing_values:
							item_attr_doc.append("item_attribute_values", {
								"attribute_value": t_name,
								"abbr": t_name[:10],
							})
							existing_values.add(t_name.lower())

					item_attr_doc.flags.ignore_permissions = True
					item_attr_doc.save(ignore_permissions=True)
					woo_attr.erpnext_item_attribute = item_attr_doc.name
				except Exception as e:
					logger.error(f"Failed to sync Item Attribute {attr_name}: {e}")

				woo_attr.save(ignore_permissions=True)
				total_attrs += 1

			page += 1

		frappe.db.commit()

		msg = _("Successfully fetched {0} attribute(s) and {1} term(s) from WooCommerce!").format(total_attrs, total_terms)
		if frappe.request:
			frappe.msgprint(msg, title=_("Attributes Synced"), indicator="green")

		return {"attributes_synced": total_attrs, "terms_synced": total_terms, "message": msg}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="woo_prime: Fetch Attributes Failed", message=frappe.get_traceback())
		frappe.throw(_("Attribute fetch failed: {0}").format(str(e)))

