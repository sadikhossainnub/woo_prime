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
def sync_categories_from_woo():
	"""Fetch all product categories from WooCommerce and sync to ERPNext.

	- Paginates through all categories (100 per page)
	- Creates new / updates existing Woo Category documents
	- Resolves parent-child hierarchy using WooCommerce parent IDs
	- Sets is_group for categories that have children
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	try:
		api = get_woo_api()
		page = 1
		total_synced = 0
		all_categories = []

		# --- Pass 1: Fetch all categories from WooCommerce ---
		while True:
			response = api.get("products/categories", params={"per_page": 100, "page": page})
			if response.status_code != 200:
				frappe.throw(_(
					"Failed to fetch categories from WooCommerce: {0}"
				).format(response.text[:300]))

			categories = response.json()
			if not categories:
				break

			all_categories.extend(categories)
			page += 1

		# Build lookup: WooCommerce category ID → category data
		woo_id_to_data = {cat.get("id"): cat for cat in all_categories}

		# Determine which WooCommerce IDs are parents (have children)
		parent_ids = set()
		for cat_data in all_categories:
			parent_woo_id = cat_data.get("parent")
			if parent_woo_id and parent_woo_id != 0:
				parent_ids.add(parent_woo_id)

		# Map: WooCommerce category ID → ERPNext Woo Category name
		woo_id_to_name = {}

		# --- Pass 2: Create / Update categories in ERPNext (parents first) ---
		# Sort so that root categories (parent=0) are created before children
		sorted_categories = sorted(all_categories, key=lambda c: (c.get("parent") or 0))

		for cat_data in sorted_categories:
			cat_id = cat_data.get("id")
			cat_name = cat_data.get("name")
			slug = cat_data.get("slug")
			description = cat_data.get("description", "")
			parent_woo_id = cat_data.get("parent")

			if not cat_name:
				continue

			# Find by woo_category_id first, then by category_name
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

			# Set parent category
			if parent_woo_id and parent_woo_id != 0:
				parent_name = woo_id_to_name.get(parent_woo_id)
				if not parent_name:
					parent_name = frappe.db.get_value("Woo Category", {"woo_category_id": parent_woo_id})
				if parent_name:
					cat_doc.parent_woo_category = parent_name
			else:
				cat_doc.parent_woo_category = None

			cat_doc.save(ignore_permissions=True)
			total_synced += 1

			woo_id_to_name[cat_id] = cat_doc.name

		frappe.db.commit()

		# Rebuild the tree to fix lft/rgt values
		rebuild_tree("Woo Category", "parent_woo_category")
		frappe.db.commit()

		frappe.msgprint(
			_("✅ Successfully synced {0} product categories from WooCommerce!").format(total_synced),
			title=_("Categories Synced"),
			indicator="green",
		)
		return total_synced

	except Exception as e:
		frappe.throw(_("Category sync error: {0}").format(str(e)))


@frappe.whitelist()
def get_children(doctype, parent=None, is_root=False, **filters):
	"""Return child categories for tree view."""
	cond_filters = {"ifnull(`parent_woo_category`, '')": ""}
	if not is_root:
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
