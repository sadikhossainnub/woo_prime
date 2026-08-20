# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WooCategory(Document):
	def validate(self):
		if not self.slug and self.category_name:
			self.slug = frappe.scrub(self.category_name).replace("_", "-")


@frappe.whitelist()
def sync_categories_from_woo():
	"""Fetch all product categories from WooCommerce and sync to ERPNext."""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	try:
		api = get_woo_api()
		page = 1
		total_synced = 0

		while True:
			response = api.get("products/categories", params={"per_page": 100, "page": page})
			if response.status_code != 200:
				frappe.throw(_("Failed to fetch categories from WooCommerce: {0}").format(response.text[:300]))

			categories = response.json()
			if not categories:
				break

			for cat_data in categories:
				cat_id = cat_data.get("id")
				cat_name = cat_data.get("name")
				slug = cat_data.get("slug")
				description = cat_data.get("description", "")

				# Find by woo_category_id or category_name
				existing_name = frappe.db.get_value("Woo Category", {"woo_category_id": cat_id}) or frappe.db.get_value("Woo Category", {"category_name": cat_name})

				if existing_name:
					cat_doc = frappe.get_doc("Woo Category", existing_name)
				else:
					cat_doc = frappe.new_doc("Woo Category")
					cat_doc.category_name = cat_name

				cat_doc.woo_category_id = cat_id
				cat_doc.slug = slug
				cat_doc.description = description
				cat_doc.save(ignore_permissions=True)
				total_synced += 1

			page += 1

		frappe.db.commit()
		frappe.msgprint(
			_("✅ Successfully synced {0} product categories from WooCommerce!").format(total_synced),
			title=_("Categories Synced"),
			indicator="green",
		)
		return total_synced

	except Exception as e:
		frappe.throw(_("Category sync error: {0}").format(str(e)))
