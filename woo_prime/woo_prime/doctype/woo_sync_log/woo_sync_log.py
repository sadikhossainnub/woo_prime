# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import json
import frappe
from frappe.model.document import Document


def _format_payload(data):
	"""Ensure data is formatted as pretty-printed JSON string for Code (JSON) fields."""
	if data is None:
		return None
	if isinstance(data, (dict, list)):
		try:
			return json.dumps(data, indent=2, default=str)
		except Exception:
			return str(data)
	if isinstance(data, str):
		data_str = data.strip()
		if not data_str:
			return None
		if (data_str.startswith("{") and data_str.endswith("}")) or (data_str.startswith("[") and data_str.endswith("]")):
			try:
				parsed = json.loads(data_str)
				return json.dumps(parsed, indent=2, default=str)
			except Exception:
				return data_str
		return data_str
	return str(data)


class WooSyncLog(Document):
	def validate(self):
		if self.request_data:
			self.request_data = _format_payload(self.request_data)
		if self.response_data:
			self.response_data = _format_payload(self.response_data)

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
				woo_item.publish_to_woocommerce(background=False)
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
					frappe.db.commit()
					frappe.msgprint(frappe._("✅ Order sync retried successfully!"), indicator="green")
					return True
				except Exception as e:
					self.db_set("status", "Failed")
					self.db_set("error_message", str(e))
					frappe.db.commit()
					frappe.throw(frappe._("Order retry failed: {0}").format(str(e)))
			elif self.woo_reference_id:
				try:
					from woo_prime.woo_prime.doctype.woo_settings.woo_settings import fetch_missing_order
					fetch_missing_order(self.woo_reference_id)
					self.db_set("status", "Success")
					self.db_set("error_message", None)
					frappe.db.commit()
					return True
				except Exception as e:
					self.db_set("status", "Failed")
					self.db_set("error_message", str(e))
					frappe.db.commit()
					frappe.throw(frappe._("Order retry failed: {0}").format(str(e)))

		frappe.throw(frappe._("Unable to automatically retry this type of log entry."))


def create_log(sync_type, direction, status, reference_doctype=None, reference_name=None,
               woo_reference_id=None, request_data=None, response_data=None, error_message=None, **kwargs):
	"""Create a Woo Sync Log entry.

	Args:
		sync_type: Order / Item / Stock / Price / Customer
		direction: Incoming / Outgoing
		status: Queued / Success / Failed
		reference_doctype: Related doctype (e.g., Sales Order, Woo Item)
		reference_name: Related document name
		woo_reference_id: WooCommerce order/product ID
		request_data: Request payload (dict, list, or JSON string)
		response_data: Response payload (dict, list, or JSON string)
		error_message: Short error description
	"""
	try:
		if not error_message and kwargs.get("message"):
			error_message = kwargs.get("message")

		ref_id_str = str(woo_reference_id) if woo_reference_id else None

		req_str = _format_payload(request_data)
		resp_str = _format_payload(response_data)

		# If response_data is missing but error_message is present, populate response_data
		if not resp_str and error_message:
			resp_str = _format_payload({"error": error_message})
		elif not resp_str and status == "Success":
			resp_str = _format_payload({
				"status": "Success",
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
			})

		# If request_data is missing, provide context payload so it is not blank
		if not req_str and (reference_name or ref_id_str):
			req_str = _format_payload({
				"sync_type": sync_type,
				"direction": direction,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"woo_reference_id": ref_id_str,
			})

		# Do not create a new "Failed" log row if one for the same woo_reference_id already exists; update it.
		if status == "Failed" and ref_id_str:
			existing_log_name = frappe.db.get_value(
				"Woo Sync Log",
				{"sync_type": sync_type, "woo_reference_id": ref_id_str, "status": "Failed"},
				"name",
			)
			if existing_log_name:
				log = frappe.get_doc("Woo Sync Log", existing_log_name)
				log.direction = direction
				log.reference_doctype = reference_doctype or log.reference_doctype
				log.reference_name = reference_name or log.reference_name
				if req_str:
					log.request_data = req_str
				if resp_str:
					log.response_data = resp_str
				log.error_message = error_message
				log.save(ignore_permissions=True)
				return log.name

		log = frappe.new_doc("Woo Sync Log")
		log.sync_type = sync_type
		log.direction = direction
		log.status = status
		log.reference_doctype = reference_doctype
		log.reference_name = reference_name
		log.woo_reference_id = ref_id_str
		log.request_data = req_str
		log.response_data = resp_str
		log.error_message = error_message
		log.insert(ignore_permissions=True)
		return log.name
	except Exception:
		frappe.log_error("Woo Sync Log Creation Failed")
		return None
