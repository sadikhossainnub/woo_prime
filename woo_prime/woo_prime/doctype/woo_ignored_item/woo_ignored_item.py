# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WooIgnoredItem(Document):
	pass


def create_tombstone(item_code=None, sku=None, woo_product_id=None, reason="User deleted"):
	"""Create a tombstone record for a deleted Woo Item."""
	if not item_code and not sku:
		return None
	
	# Check if tombstone already exists
	existing = frappe.db.exists("Woo Ignored Item", {"item_code": item_code}) if item_code else None
	if not existing and sku:
		existing = frappe.db.exists("Woo Ignored Item", {"sku": sku})
	
	if existing:
		return existing
	
	try:
		doc = frappe.new_doc("Woo Ignored Item")
		if item_code:
			doc.item_code = item_code
		if sku:
			doc.sku = sku
		if woo_product_id:
			doc.woo_product_id = woo_product_id
		doc.reason = reason
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name
	except Exception:
		frappe.log_error(title="Failed to create tombstone", message=frappe.get_traceback())
		return None


def remove_tombstone(item_code=None, sku=None):
	"""Remove tombstone to allow item restoration."""
	filters = {}
	if item_code:
		filters["item_code"] = item_code
	elif sku:
		filters["sku"] = sku
	else:
		return False
	
	tombstones = frappe.get_all("Woo Ignored Item", filters=filters, pluck="name")
	for name in tombstones:
		frappe.delete_doc("Woo Ignored Item", name, ignore_permissions=True, force=True)
	
	return len(tombstones) > 0


def has_tombstone(item_code=None, sku=None):
	"""Check if a tombstone exists for this item."""
	if item_code and frappe.db.exists("Woo Ignored Item", {"item_code": item_code}):
		return True
	if sku and frappe.db.exists("Woo Ignored Item", {"sku": sku}):
		return True
	return False
