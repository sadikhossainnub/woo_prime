# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import hashlib
import hmac
import json
import base64

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_order():
	"""Webhook endpoint for WooCommerce order events.

	WooCommerce sends a POST request with order data when an order is created/updated.
	This endpoint:
	1. Verifies the HMAC-SHA256 signature
	2. Checks for idempotency (duplicate order)
	3. Enqueues order processing in background
	"""
	try:
		# Get the raw request data
		data = frappe.request.get_data(as_text=True)

		if not data:
			frappe.throw(_("Empty request body"), frappe.ValidationError)

		# Check if this is a WooCommerce webhook ping (test delivery)
		wc_topic = frappe.request.headers.get("X-WC-Webhook-Topic", "")
		if not wc_topic:
			# Could be a ping/test
			return {"status": "ok", "message": "Webhook endpoint is active"}

		# Verify HMAC signature
		verify_webhook_signature(data)

		# Parse the order data
		order_data = json.loads(data)

		# Handle ping event
		if wc_topic == "order.deleted":
			return {"status": "ok", "message": "Order deletion noted (no action taken)"}

		# Get the WooCommerce order ID
		woo_order_id = order_data.get("id")
		if not woo_order_id:
			frappe.throw(_("No order ID found in webhook data"), frappe.ValidationError)

		# Idempotency check — skip if Sales Order already exists for this WooCommerce order
		existing_so = frappe.db.get_value(
			"Sales Order",
			{"woo_order_id": str(woo_order_id)},
			"name"
		)

		if existing_so:
			# Order already synced — if it's an update, we could update the SO
			if wc_topic == "order.updated":
				frappe.enqueue(
					"woo_prime.api.sync.update_order_from_woo",
					queue="default",
					order_data=order_data,
					existing_so=existing_so,
				)
				return {
					"status": "ok",
					"message": f"Order update queued for SO: {existing_so}",
				}
			return {
				"status": "skipped",
				"message": f"Order {woo_order_id} already synced as {existing_so}",
			}

		# Log the incoming request
		from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

		create_log(
			sync_type="Order",
			direction="Incoming",
			status="Queued",
			woo_reference_id=str(woo_order_id),
			request_data=data[:10000],  # Limit stored data size
		)

		# Enqueue order sync in background for non-blocking response
		frappe.enqueue(
			"woo_prime.api.sync.sync_order",
			queue="default",
			order_data=order_data,
			enqueue_after_commit=True,
		)

		return {
			"status": "ok",
			"message": f"Order {woo_order_id} queued for processing",
		}

	except frappe.ValidationError:
		raise
	except Exception as e:
		frappe.log_error(
			title="WooCommerce Webhook Error",
			message=frappe.get_traceback(),
		)
		# Return 200 to prevent WooCommerce from disabling the webhook
		return {"status": "error", "message": str(e)}


def verify_webhook_signature(payload):
	"""Verify the WooCommerce webhook HMAC-SHA256 signature.

	Args:
		payload: Raw request body string
	"""
	settings = frappe.get_single("Woo Settings")
	webhook_secret = settings.get_password("webhook_secret")

	if not webhook_secret:
		# If no webhook secret is configured, skip verification
		return

	signature = frappe.request.headers.get("X-WC-Webhook-Signature", "")
	if not signature:
		frappe.throw(
			_("Missing webhook signature header"),
			frappe.AuthenticationError,
		)

	# WooCommerce uses HMAC-SHA256 and base64 encodes the result
	computed_hash = base64.b64encode(
		hmac.new(
			webhook_secret.encode("utf-8"),
			payload.encode("utf-8"),
			hashlib.sha256,
		).digest()
	).decode("utf-8")

	if not hmac.compare_digest(computed_hash, signature):
		frappe.throw(
			_("Invalid webhook signature — request rejected"),
			frappe.AuthenticationError,
		)
