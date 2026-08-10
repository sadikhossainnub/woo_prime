# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WooSyncLog(Document):
	pass


def create_log(sync_type, direction, status, reference_doctype=None, reference_name=None,
               woo_reference_id=None, request_data=None, response_data=None, error_message=None):
	"""Create a Woo Sync Log entry.

	Args:
		sync_type: Order / Item / Stock / Price / Customer
		direction: Incoming / Outgoing
		status: Queued / Success / Failed
		reference_doctype: Related doctype (e.g., Sales Order, Woo Item)
		reference_name: Related document name
		woo_reference_id: WooCommerce order/product ID
		request_data: Full request payload (JSON string)
		response_data: Full response payload (JSON string)
		error_message: Short error description
	"""
	try:
		log = frappe.new_doc("Woo Sync Log")
		log.sync_type = sync_type
		log.direction = direction
		log.status = status
		log.reference_doctype = reference_doctype
		log.reference_name = reference_name
		log.woo_reference_id = str(woo_reference_id) if woo_reference_id else None
		log.request_data = request_data
		log.response_data = response_data
		log.error_message = error_message
		log.insert(ignore_permissions=True)
		frappe.db.commit()
		return log.name
	except Exception:
		frappe.log_error("Woo Sync Log Creation Failed")
		return None
