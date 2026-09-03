# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WooSyncLog(Document):
	@frappe.whitelist()
	def retry_sync(self):
		"""Retry a failed sync operation."""
		if self.status == "Success":
			frappe.msgprint(frappe._("This sync log is already marked as Success."))
			return

		if self.sync_type in ("Item", "Stock", "Price") and self.reference_doctype == "Woo Item" and self.reference_name:
			if not frappe.db.exists("Woo Item", self.reference_name):
				frappe.throw(frappe._("Referenced Woo Item {0} no longer exists.").format(self.reference_name))

			woo_item = frappe.get_doc("Woo Item", self.reference_name)
			if self.sync_type == "Item":
				woo_item.publish_to_woocommerce()
			elif self.sync_type == "Stock":
				woo_item.sync_stock_now()
			elif self.sync_type == "Price":
				woo_item.sync_price_now()

			self.db_set("status", "Success")
			self.db_set("error_message", None)
			frappe.msgprint(frappe._("✅ Successfully retried sync operation!"), indicator="green")
			return True

		elif self.sync_type == "Order":
			import json
			from woo_prime.api.sync import sync_order

			if self.request_data:
				try:
					order_data = json.loads(self.request_data)
					sync_order(order_data)
					self.db_set("status", "Success")
					self.db_set("error_message", None)
					frappe.msgprint(frappe._("✅ Order sync retried successfully!"), indicator="green")
					return True
				except Exception as e:
					frappe.throw(frappe._("Order retry failed: {0}").format(str(e)))
			elif self.woo_reference_id:
				from woo_prime.woo_prime.doctype.woo_settings.woo_settings import fetch_missing_order
				fetch_missing_order(self.woo_reference_id)
				self.db_set("status", "Success")
				self.db_set("error_message", None)
				return True

		frappe.throw(frappe._("Unable to automatically retry this type of log entry."))


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
