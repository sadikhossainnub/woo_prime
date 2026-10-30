# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import html
import json

import frappe
from frappe import _
from frappe.utils import now_datetime, flt, cstr, today, add_days
from woo_prime.utils import safe_log_error, clean_text


def sync_order(order_data):
	"""Sync a WooCommerce order to ERPNext Sales Order.

	This is called from the webhook handler via frappe.enqueue().

	Args:
		order_data: dict — WooCommerce order payload
	"""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled:
			return

		# Run as the configured integration user to avoid guest-context permission failures
		integration_user = getattr(settings, "integration_user", None) or "Administrator"
		frappe.set_user(integration_user)

		woo_order_id = order_data.get("id")

		# Check order status against configured sync_order_statuses
		order_status = (order_data.get("status") or "").strip().lower()
		allowed_statuses = [
			s.strip().lower() for s in (getattr(settings, "sync_order_statuses", None) or "processing,completed").split(",") if s.strip()
		]
		if order_status and allowed_statuses and order_status not in allowed_statuses:
			from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
			create_log(
				sync_type="Order",
				direction="Incoming",
				status="Skipped",
				woo_reference_id=str(woo_order_id),
				response_data=json.dumps({"reason": f"Order status '{order_status}' not in sync_order_statuses ({allowed_statuses})"}),
			)
			return

		# Double-check idempotency (in case of race condition)
		existing_so = frappe.db.get_value("Sales Order", {"woo_order_id": str(woo_order_id)}, "name")
		if existing_so:
			return existing_so

		# Savepoint to ensure failures leave no orphan customer/address/contact
		savepoint_name = "woo_order"
		frappe.db.savepoint(savepoint_name)

		# 1. Find or create customer and addresses
		customer_name, billing_address, shipping_address = _get_or_create_customer(order_data, settings)

		# Transaction date & Delivery date (+7 days)
		transaction_date = _parse_date(order_data.get("date_created", today()))
		delivery_date = add_days(transaction_date, 7)

		# 2. Map line items
		items, unmapped_lines = _map_order_items(order_data, settings, delivery_date=delivery_date)

		if unmapped_lines:
			frappe.db.rollback(save_point=savepoint_name)
			unmapped_desc = ", ".join(f"{u.get('name') or u.get('sku') or u.get('id')}" for u in unmapped_lines)
			err_msg = f"Cannot create Sales Order: {len(unmapped_lines)} unmapped WooCommerce line item(s): {unmapped_desc}"
			_log_error(woo_order_id, order_data, err_msg)
			frappe.db.commit()
			frappe.throw(err_msg)

		if not items:
			frappe.db.rollback(save_point=savepoint_name)
			err_msg = "No valid items found in order"
			_log_error(woo_order_id, order_data, err_msg)
			frappe.db.commit()
			frappe.throw(err_msg)

		# 3. Create Sales Order
		so = frappe.new_doc("Sales Order")
		so.customer = customer_name
		so.order_type = "Shopping Cart"
		if getattr(settings, "sales_type", None) and frappe.get_meta("Sales Order").has_field("sales_type"):
			so.sales_type = settings.sales_type
		so.company = settings.default_company
		so.transaction_date = transaction_date
		so.delivery_date = delivery_date
		so.currency = order_data.get("currency", "BDT")
		so.selling_price_list = settings.default_price_list
		so.set_warehouse = settings.default_warehouse

		if billing_address:
			so.customer_address = billing_address
		if shipping_address:
			so.shipping_address_name = shipping_address

		# Ignore ERPNext internal pricing rules since WooCommerce calculated final order prices
		so.ignore_pricing_rule = 1

		# Handle Coupons & Discounts from WooCommerce
		coupon_lines = order_data.get("coupon_lines", [])
		discount_total = flt(order_data.get("discount_total", 0))

		loyalty_discount = 0
		if coupon_lines:
			coupons_applied = [c.get("code") for c in coupon_lines if c.get("code")]
			so.po_no = f"WC-{woo_order_id} (Coupons: {', '.join(coupons_applied)})"

			# Check if any coupon is for Loyalty Points redemption
			for coupon in coupon_lines:
				code = str(coupon.get("code", "")).lower()
				discount = flt(coupon.get("discount", 0))
				if "loyalty" in code or "point" in code:
					loyalty_discount += discount
					so.loyalty_amount = loyalty_discount

		# If loyalty_amount handling does not reduce grand_total, treat loyalty coupon discount as normal discount_amount
		has_active_loyalty = bool(
			getattr(so, "loyalty_amount", 0)
			and getattr(so, "loyalty_program", None)
			and getattr(so, "loyalty_redemption_account", None)
		)

		if not has_active_loyalty and loyalty_discount > 0 and discount_total == 0:
			discount_total = loyalty_discount

		if discount_total > 0:
			if has_active_loyalty:
				so.discount_amount = max(0, discount_total - flt(so.loyalty_amount))
			else:
				so.discount_amount = discount_total
				so.loyalty_amount = 0
			so.apply_discount_on = "Net Total"

		# Custom fields for WooCommerce tracking
		so.woo_order_id = str(woo_order_id)
		so.woo_order_status = order_data.get("status", "")
		so.woo_site = settings.woo_site_url

		# Add PO reference if not set
		if not so.po_no:
			so.po_no = f"WC-{woo_order_id}"

		# Add line items
		for item in items:
			so.append("items", item)

		# Shipping details from WooCommerce payload
		shipping_total = flt(order_data.get("shipping_total", 0))
		shipping_lines = order_data.get("shipping_lines", [])
		shipping_title = shipping_lines[0].get("method_title", "Shipping") if shipping_lines else "Shipping Charges"

		# Resolve ERPNext Shipping Rule from WooCommerce Zone/Method Title or Mappings (only when shipping_total > 0)
		matched_rule = None
		if shipping_total > 0:
			if shipping_lines and hasattr(settings, "shipping_rule_mappings") and settings.shipping_rule_mappings:
				s_line = shipping_lines[0]
				method_title = (s_line.get("method_title") or "").strip().lower()
				method_id = (s_line.get("method_id") or "").strip().lower()

				for mapping in settings.shipping_rule_mappings:
					target_method = (mapping.woo_shipping_method or "").strip().lower()
					if target_method and (
						target_method == method_title
						or target_method == method_id
						or target_method in method_title
						or (method_title and method_title in target_method)
					):
						matched_rule = mapping.shipping_rule
						break

			if not matched_rule and shipping_title and frappe.db.exists("Shipping Rule", shipping_title):
				matched_rule = shipping_title

			if not matched_rule and getattr(settings, "default_shipping_rule", None):
				matched_rule = settings.default_shipping_rule

			if matched_rule:
				so.shipping_rule = matched_rule
			else:
				# Add shipping charges via Taxes or Line item only if shipping_rule is not matched
				shipping_sync_mode = getattr(settings, "shipping_sync_type", "Line Item")
				if shipping_sync_mode == "Taxes and Charges Table" or getattr(settings, "shipping_charge_account", None):
					account_head = getattr(settings, "shipping_charge_account", None)
					if not account_head and settings.default_company:
						account_head = frappe.db.get_value(
							"Account",
							{"company": settings.default_company, "account_name": ["like", "%Shipping%"], "is_group": 0},
							"name"
						)

					if account_head:
						so.append("taxes", {
							"charge_type": "Actual",
							"account_head": account_head,
							"description": f"Shipping Charges ({shipping_title})",
							"tax_amount": shipping_total,
						})
					elif settings.shipping_item:
						so.append("items", {
							"item_code": settings.shipping_item,
							"item_name": f"Shipping - {shipping_title}",
							"qty": 1,
							"rate": shipping_total,
							"delivery_date": so.delivery_date,
							"warehouse": settings.default_warehouse,
						})
				elif settings.shipping_item:
					so.append("items", {
						"item_code": settings.shipping_item,
						"item_name": f"Shipping - {shipping_title}",
						"qty": 1,
						"rate": shipping_total,
						"delivery_date": so.delivery_date,
						"warehouse": settings.default_warehouse,
					})

		# Add taxes from template if configured
		if settings.tax_template:
			so.taxes_and_charges = settings.tax_template
			so.set_taxes()

		# Fee lines handling (surcharges, COD fees, packaging charges)
		fee_lines = order_data.get("fee_lines", [])
		if fee_lines:
			for fee in fee_lines:
				fee_title = fee.get("name") or "Additional Fee"
				fee_total = flt(fee.get("total", 0))
				if fee_total != 0:
					account_head = getattr(settings, "shipping_charge_account", None)
					if not account_head and settings.default_company:
						account_head = frappe.db.get_value(
							"Account",
							{"company": settings.default_company, "account_name": ["like", "%Fee%"], "is_group": 0},
							"name"
						)
					if account_head:
						so.append("taxes", {
							"charge_type": "Actual",
							"account_head": account_head,
							"description": f"WooCommerce Fee: {fee_title}",
							"tax_amount": fee_total,
						})
					elif settings.shipping_item:
						so.append("items", {
							"item_code": settings.shipping_item,
							"item_name": f"Fee - {fee_title}",
							"qty": 1,
							"rate": fee_total,
							"delivery_date": so.delivery_date,
							"warehouse": settings.default_warehouse,
						})

		# Customer notes
		customer_note = order_data.get("customer_note", "")

		if customer_note:
			so.add_comment("Comment", text=f"Customer Note (WooCommerce): {customer_note}")

		# Recalculate taxes and totals to ensure line items, shipping and fee additions are accurately computed
		try:
			so.calculate_taxes_and_totals()
		except Exception:
			pass

		# Compare so.grand_total with WooCommerce order total
		woo_total = flt(order_data.get("total", 0))
		tolerance = flt(getattr(settings, "order_total_tolerance", 1.0) or 1.0)
		diff = abs(flt(so.grand_total) - woo_total)
		is_mismatch = diff > tolerance

		# Save Sales Order
		try:
			so.flags.ignore_permissions = True
			so.flags.ignore_mandatory = True
			so.save()

			if is_mismatch:
				mismatch_msg = (
					f"Total mismatch for WooCommerce Order WC-{woo_order_id}: "
					f"ERPNext Grand Total is {so.grand_total}, but WooCommerce Order Total is {woo_total} "
					f"(diff: {diff:.2f} > tolerance: {tolerance:.2f}). Saved as Draft."
				)
				so.add_comment("Comment", text=mismatch_msg)
				_log_error(woo_order_id, order_data, mismatch_msg)
				frappe.db.commit()
				return

			if getattr(settings, "auto_submit_order", 1):
				so.flags.from_woo = True
				so.submit()
		except frappe.DuplicateEntryError:
			frappe.logger("woo_prime").info(f"Sales Order for WooCommerce Order WC-{woo_order_id} already exists. Skipping duplicate insert.")
			return

		# Send email notification if enabled
		if getattr(settings, "order_email_notification", 0) and getattr(settings, "notification_email", None):
			try:
				subject = f"🛒 New WooCommerce Order: WC-{woo_order_id} ({so.name})"
				message = f"""
				<h3>New Order Received from WooCommerce</h3>
				<p><b>Order ID:</b> WC-{woo_order_id}</p>
				<p><b>Sales Order:</b> {so.name}</p>
				<p><b>Customer:</b> {customer_name}</p>
				<p><b>Total Amount:</b> {so.currency} {so.grand_total}</p>
				<p><b>Status:</b> {so.woo_order_status}</p>
				<hr>
				<p><a href="{frappe.utils.get_url_to_form('Sales Order', so.name)}">View Sales Order in ERPNext</a></p>
				"""
				frappe.sendmail(
					recipients=[settings.notification_email],
					subject=subject,
					message=message,
					now=True,
				)
			except Exception:
				frappe.log_error(title=f"Order Email Notification Failed - {so.name}", message=frappe.get_traceback())

		# Update sync log
		from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

		create_log(
			sync_type="Order",
			direction="Incoming",
			status="Success",
			reference_doctype="Sales Order",
			reference_name=so.name,
			woo_reference_id=str(woo_order_id),
			request_data=order_data,
			response_data={"sales_order": so.name, "customer": customer_name, "grand_total": so.grand_total},
		)

		frappe.db.commit()
		return so.name

	except Exception as e:
		frappe.db.rollback(save_point="woo_order")
		frappe.log_error(
			title=f"WooCommerce Order Sync Failed - {order_data.get('id', 'Unknown')}",
			message=frappe.get_traceback(),
		)
		_log_error(order_data.get("id"), order_data, str(e))
		frappe.db.commit()
		raise


def update_order_from_woo(order_data, existing_so):
	"""Update an existing Sales Order from WooCommerce order update.

	Args:
		order_data: dict — WooCommerce order payload
		existing_so: str — existing Sales Order name
	"""
	try:
		settings = frappe.get_single("Woo Settings")
		integration_user = getattr(settings, "integration_user", None) or "Administrator"
		frappe.set_user(integration_user)

		so = frappe.get_doc("Sales Order", existing_so)

		# Only update status-related fields, don't modify items
		new_status = (order_data.get("status") or "").strip().lower()
		so.db_set("woo_order_status", new_status)

		if new_status in ("cancelled", "failed", "refunded") and so.docstatus == 1:
			so.flags.from_woo = True
			so.cancel()

		from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

		create_log(
			sync_type="Order",
			direction="Incoming",
			status="Success",
			reference_doctype="Sales Order",
			reference_name=existing_so,
			woo_reference_id=str(order_data.get("id")),
			request_data=order_data,
			response_data={"action": "status_update", "new_status": new_status, "sales_order": existing_so},
		)
	except Exception as e:
		frappe.log_error(
			title=f"WooCommerce Order Update Failed - {existing_so}",
			message=frappe.get_traceback(),
		)


def _normalize_phone(phone):
	"""Extract digits from phone string; returns full digit string (10+ digits for exact match)."""
	if not phone:
		return ""
	digits = "".join(c for c in str(phone) if c.isdigit())
	return digits


def _get_or_create_customer(order_data, settings):
	"""Find existing customer or create new one from WooCommerce billing data.

	Matching priority:
	1. Match by email (exact, on Customer.email_id)
	2. Match by normalized phone (full digits, 10+, exact match — no LIKE)
	3. Match by linked Contact (via Contact Email or Contact Phone child tables)
	4. Name-only fallback only if BOTH email and phone are empty
	5. Auto-create new Customer if not found

	Args:
		order_data: dict — WooCommerce order payload
		settings: Woo Settings document

	Returns:
		tuple: (customer_name, billing_address_name, shipping_address_name)
	"""
	billing = order_data.get("billing", {})
	email = (billing.get("email") or "").strip()
	phone = (billing.get("phone") or "").strip()
	first_name = (billing.get("first_name") or "").strip()
	last_name = (billing.get("last_name") or "").strip()
	company_name = (billing.get("company") or "").strip()

	full_name = f"{first_name} {last_name}".strip()
	if not full_name:
		full_name = email or f"WooCommerce Customer #{order_data.get('id', '')}"

	customer_name = None

	# 1. Try to find existing customer by email (exact match)
	if email:
		existing = frappe.db.get_value("Customer", {"email_id": email}, "name")
		if existing:
			customer_name = existing

	# 2. Try to find by normalized phone (exact full-digit match, no LIKE)
	if not customer_name and phone:
		clean_phone = _normalize_phone(phone)
		if clean_phone and len(clean_phone) >= 10:
			# Exact match on mobile_no (raw) first, then normalized digits
			existing = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")
			if not existing and clean_phone != phone:
				existing = frappe.db.get_value("Customer", {"mobile_no": clean_phone}, "name")
			if existing:
				customer_name = existing

	# 3. Try to find by Contact record (Contact Email or Contact Phone → Dynamic Link)
	if not customer_name and (email or phone):
		contact_name = None

		if email:
			# Check Contact Email child table
			contact_name = frappe.db.get_value(
				"Contact Email",
				{"email_id": email, "parenttype": "Contact"},
				"parent",
			)

		if not contact_name and phone:
			# Check Contact Phone child table — exact phone match
			contact_name = frappe.db.get_value(
				"Contact Phone",
				{"phone": phone, "parenttype": "Contact"},
				"parent",
			)

		if contact_name:
			linked_customer = frappe.db.get_value(
				"Dynamic Link",
				{"link_doctype": "Customer", "parenttype": "Contact", "parent": contact_name},
				"link_name",
			)
			if linked_customer:
				customer_name = linked_customer

	# 4. Name-only fallback ONLY when both email and phone are empty
	target_title = company_name or full_name
	if not customer_name and not email and not phone and target_title:
		if frappe.db.exists("Customer", target_title):
			customer_name = target_title

	# 5. Auto-create new customer if not found
	if not customer_name:
		customer_group = getattr(settings, "default_customer_group", None)
		if not customer_group:
			# Get first non-group Customer Group
			customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
			if not customer_group:
				frappe.throw(_("No non-group Customer Group found. Please create at least one non-group Customer Group or set a default in Woo Prime Settings."))

		territory = getattr(settings, "default_territory", None)
		if not territory:
			# Get first non-group Territory
			territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
			if not territory:
				frappe.throw(_("No non-group Territory found. Please create at least one non-group Territory or set a default in Woo Prime Settings."))

		customer = frappe.new_doc("Customer")
		customer.customer_name = target_title
		customer.customer_type = "Company" if company_name else "Individual"
		customer.customer_group = customer_group
		customer.territory = territory

		if email:
			customer.email_id = email
		if phone:
			customer.mobile_no = phone

		customer.flags.ignore_permissions = True
		customer.flags.ignore_mandatory = True

		try:
			customer.insert()
			customer_name = customer.name
		except Exception:
			# If naming collision occurred, try adding phone/email to distinguish name
			if frappe.db.exists("Customer", target_title):
				customer_name = target_title
			else:
				distinguisher = phone or email or order_data.get("id", "")
				customer.customer_name = f"{target_title} ({distinguisher})"
				customer.insert()
				customer_name = customer.name

	# Ensure address and contact exist
	billing_addr, shipping_addr = _get_or_create_address(
		customer_name,
		billing,
		order_data.get("shipping", {}),
	)

	# Create contact
	if email or phone:
		_create_contact(customer_name, first_name, last_name, email, phone)

	# Log customer creation / sync
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	create_log(
		sync_type="Customer",
		direction="Incoming",
		status="Success",
		reference_doctype="Customer",
		reference_name=customer_name,
		woo_reference_id=str(order_data.get("customer_id", "")),
		request_data=billing,
		response_data={"customer_name": customer_name, "billing_address": billing_addr, "shipping_address": shipping_addr},
	)

	return customer_name, billing_addr, shipping_addr


def _get_or_create_address(customer_name, billing, shipping):
	"""Create or find billing and shipping addresses for a customer.

	Reuses an existing Address only if address_line1 AND city both match.
	Otherwise creates a new Address to avoid stale data.

	Args:
		customer_name: str — ERPNext customer name
		billing: dict — WooCommerce billing address data
		shipping: dict — WooCommerce shipping address data

	Returns:
		tuple: (billing_address_name, shipping_address_name)
	"""
	billing_addr_name = None
	shipping_addr_name = None

	def _find_existing_address(customer_name, address_line1, city, address_type):
		"""Find an existing address linked to the customer matching address_line1 and city."""
		if not address_line1 and not city:
			return None
		# Get all addresses linked to this customer
		linked_parents = frappe.db.get_all(
			"Dynamic Link",
			filters={
				"link_doctype": "Customer",
				"link_name": customer_name,
				"parenttype": "Address",
			},
			pluck="parent",
		)
		if not linked_parents:
			return None
		# Find among them one that matches address_line1 + city
		for addr_name in linked_parents:
			addr_vals = frappe.db.get_value(
				"Address",
				addr_name,
				["address_line1", "city", "address_type"],
				as_dict=True,
			)
			if not addr_vals or not isinstance(addr_vals, dict):
				continue
			if (
				(addr_vals.get("address_line1") or "").strip() == (address_line1 or "").strip()
				and (addr_vals.get("city") or "").strip() == (city or "").strip()
			):
				return addr_name
		return None

	# 1. Billing address
	if billing.get("address_1"):
		addr_line1 = billing.get("address_1", "")
		city = billing.get("city", "")
		existing_billing = _find_existing_address(customer_name, addr_line1, city, "Billing")
		if existing_billing:
			billing_addr_name = existing_billing
		else:
			addr = frappe.new_doc("Address")
			addr.address_title = customer_name
			addr.address_type = "Billing"
			addr.address_line1 = addr_line1
			addr.address_line2 = billing.get("address_2", "")
			addr.city = city
			addr.state = billing.get("state", "")
			addr.pincode = billing.get("postcode", "")
			addr.country = _get_country(billing.get("country", ""))
			addr.phone = billing.get("phone", "")
			addr.email_id = billing.get("email", "")
			addr.append("links", {
				"link_doctype": "Customer",
				"link_name": customer_name,
			})
			addr.flags.ignore_permissions = True
			addr.flags.ignore_mandatory = True
			try:
				addr.insert()
				billing_addr_name = addr.name
			except Exception:
				frappe.log_error("Billing Address creation failed for customer: " + customer_name)

	# 2. Shipping address (if provided and different from billing)
	has_shipping_data = shipping and (shipping.get("address_1") or shipping.get("city"))
	is_different = (
		has_shipping_data
		and (
			shipping.get("address_1") != billing.get("address_1")
			or shipping.get("city") != billing.get("city")
		)
	)

	if is_different:
		ship_line1 = shipping.get("address_1", "")
		ship_city = shipping.get("city", "")
		existing_shipping = _find_existing_address(customer_name, ship_line1, ship_city, "Shipping")
		if existing_shipping:
			shipping_addr_name = existing_shipping
		else:
			addr = frappe.new_doc("Address")
			addr.address_title = f"{customer_name} - Shipping"
			addr.address_type = "Shipping"
			addr.address_line1 = ship_line1
			addr.address_line2 = shipping.get("address_2", "")
			addr.city = ship_city
			addr.state = shipping.get("state", "")
			addr.pincode = shipping.get("postcode", "")
			addr.country = _get_country(shipping.get("country", ""))
			addr.append("links", {
				"link_doctype": "Customer",
				"link_name": customer_name,
			})
			addr.flags.ignore_permissions = True
			addr.flags.ignore_mandatory = True
			try:
				addr.insert()
				shipping_addr_name = addr.name
			except Exception:
				frappe.log_error("Shipping Address creation failed for customer: " + customer_name)
	else:
		# Shipping same as billing — reuse billing address
		shipping_addr_name = billing_addr_name

	return billing_addr_name, shipping_addr_name


def _create_contact(customer_name, first_name, last_name, email, phone):
	"""Create a contact linked to the customer (if one doesn't already exist).

	Args:
		customer_name: str — ERPNext customer name
		first_name: str
		last_name: str
		email: str
		phone: str
	"""
	try:
		# Check if a contact with this email or phone already exists for this customer
		existing_contact = None

		if email:
			contact_email_parent = frappe.db.get_value(
				"Contact Email",
				{"email_id": email, "parenttype": "Contact"},
				"parent",
			)
			if contact_email_parent:
				# Verify this contact is linked to this customer
				link_exists = frappe.db.exists(
					"Dynamic Link",
					{"parent": contact_email_parent, "link_doctype": "Customer", "link_name": customer_name},
				)
				if link_exists:
					existing_contact = contact_email_parent

		if not existing_contact and phone:
			contact_phone_parent = frappe.db.get_value(
				"Contact Phone",
				{"phone": phone, "parenttype": "Contact"},
				"parent",
			)
			if contact_phone_parent:
				link_exists = frappe.db.exists(
					"Dynamic Link",
					{"parent": contact_phone_parent, "link_doctype": "Customer", "link_name": customer_name},
				)
				if link_exists:
					existing_contact = contact_phone_parent

		if existing_contact:
			return  # Contact already exists for this customer

		# Also check Dynamic Link directly — if any contact is linked to this customer
		existing_link = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Customer", "link_name": customer_name, "parenttype": "Contact"},
			"parent",
		)
		if existing_link:
			return  # Customer already has a contact

		contact = frappe.new_doc("Contact")
		contact.first_name = first_name or customer_name
		contact.last_name = last_name or ""

		if email:
			contact.append("email_ids", {
				"email_id": email,
				"is_primary": 1,
			})

		if phone:
			contact.append("phone_nos", {
				"phone": phone,
				"is_primary_phone": 1,
			})

		contact.append("links", {
			"link_doctype": "Customer",
			"link_name": customer_name,
		})

		contact.flags.ignore_permissions = True
		contact.flags.ignore_mandatory = True
		contact.insert()
	except Exception:
		frappe.log_error("Contact creation failed for customer: " + customer_name)


def _map_order_items(order_data, settings, delivery_date=None):
	"""Map WooCommerce line items to ERPNext Sales Order items.

	Looks up each item by woo_product_id, SKU, or item_code.
	Item rate uses pre-discount rate (subtotal / quantity).

	Args:
		order_data: dict — WooCommerce order payload
		settings: Woo Settings document
		delivery_date: str — Delivery date for items

	Returns:
		tuple: (items: list or None, unmapped: list)
	"""
	items = []
	unmapped = []
	line_items = order_data.get("line_items", [])
	item_delivery_date = delivery_date or _parse_date(order_data.get("date_created", today()))

	for line in line_items:
		sku = (line.get("sku") or "").strip()
		qty = flt(line.get("quantity", 1))
		
		# Pre-discount line rate = line.subtotal / quantity
		subtotal = flt(line.get("subtotal")) if line.get("subtotal") is not None else (flt(line.get("price", 0)) * qty)
		rate = (subtotal / qty) if qty > 0 else flt(line.get("price", 0))

		item_name_wc = line.get("name", "")
		prod_id_wc = line.get("variation_id") or line.get("product_id")

		item_code = None

		# 1. Try to find in Woo Item by WooCommerce Product ID / Variation ID
		if prod_id_wc:
			woo_item = frappe.db.get_value("Woo Item", {"woo_product_id": prod_id_wc}, "item_code")
			if woo_item:
				item_code = woo_item

		# 2. Try to find in Woo Item by SKU
		if not item_code and sku:
			woo_item = (
				frappe.db.get_value("Woo Item", {"sku": sku}, "item_code")
				or frappe.db.get_value("Woo Item", sku, "item_code")
			)
			if woo_item:
				item_code = woo_item

		# 3. Fallback: try to find ERPNext Item directly by SKU as item_code
		if not item_code and sku:
			if frappe.db.exists("Item", sku):
				item_code = sku

		if not item_code:
			unmapped.append({
				"id": prod_id_wc,
				"sku": sku,
				"name": item_name_wc,
				"quantity": qty,
			})
			continue

		items.append({
			"item_code": item_code,
			"qty": qty,
			"rate": rate,
			"delivery_date": item_delivery_date,
			"warehouse": settings.default_warehouse,
		})

	if unmapped:
		return None, unmapped
	return items, []


def _parse_date(date_str):
	"""Parse WooCommerce date string to ERPNext date format.

	WooCommerce sends dates in ISO 8601 format: 2026-08-11T02:50:00

	Args:
		date_str: str — date string from WooCommerce

	Returns:
		str: date in YYYY-MM-DD format
	"""
	if not date_str:
		return today()

	try:
		# Handle ISO 8601 format
		if "T" in str(date_str):
			return str(date_str).split("T")[0]
		return str(date_str)[:10]
	except Exception:
		return today()


def _get_country(country_code):
	"""Convert country code to ERPNext country name.

	Args:
		country_code: str — ISO 3166-1 alpha-2 country code (e.g., "BD", "US")

	Returns:
		str: Country name or the code itself if not found
	"""
	if not country_code:
		return "Bangladesh"

	country_name = frappe.db.get_value("Country", {"code": country_code.lower()}, "name")
	return country_name or country_code


def _log_error(woo_order_id, order_data, error_message):
	"""Log a sync error."""
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	create_log(
		sync_type="Order",
		direction="Incoming",
		status="Failed",
		woo_reference_id=str(woo_order_id) if woo_order_id else "",
		request_data=order_data,
		response_data={"error": error_message},
		error_message=error_message,
	)


# ═══════════════════════════════════════════════════
# Item / Stock / Price Sync (ERPNext → WooCommerce)
# ═══════════════════════════════════════════════════


def publish_item_to_woo(woo_item):
	"""Publish an ERPNext item (Simple, Template, or Variant) to WooCommerce.

	Args:
		woo_item: Woo Item document

	Returns:
		dict: WooCommerce product response
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	api = get_woo_api()
	settings = frappe.get_single("Woo Settings")
	item = frappe.get_doc("Item", woo_item.item_code)

	# Handle variant items with custom product type
	if item.variant_of:
		woo_product_type = getattr(woo_item, "woo_product_type", "")
		
		if woo_product_type == "Simple Item":
			# Publish as independent simple product
			return _publish_variant_as_simple_item(woo_item, item, api, settings)
		elif woo_product_type == "Variant Item":
			# Create as new variant product (template with its own variations)
			return _publish_variant_as_new_template(woo_item, item, api, settings)
		else:
			# Default: publish as variation under parent template
			return _publish_variant_item_to_woo(woo_item, item, api, settings)
	elif item.has_variants:
		return _publish_template_item_to_woo(woo_item, item, api, settings)
	else:
		return _publish_simple_item_to_woo(woo_item, item, api, settings)


def _publish_simple_item_to_woo(woo_item, item, api, settings):
	"""Publish a simple item to WooCommerce."""
	import html
	price = _get_item_price(woo_item.item_code, settings.default_price_list)
	regular_price = flt(woo_item.regular_price) if getattr(woo_item, "regular_price", None) else flt(price)
	sale_price = flt(woo_item.sale_price) if getattr(woo_item, "sale_price", None) and flt(woo_item.sale_price) > 0 else None
	stock_qty = _get_stock_qty(woo_item.item_code, settings.default_warehouse)

	manage_stock = bool(getattr(woo_item, "manage_stock", 1))
	product_name = woo_item.item_name or item.item_name or ""
	product_data = {
		"name": html.unescape(product_name),
		"sku": woo_item.sku,
		"regular_price": str(flt(regular_price)),
		"description": html.unescape(woo_item.woo_description or item.description or ""),
		"short_description": html.unescape(getattr(woo_item, "woo_short_description", None) or item.description or ""),
		"manage_stock": manage_stock,
		"status": "publish",
	}
	if manage_stock:
		product_data["stock_quantity"] = int(stock_qty)
		product_data["stock_status"] = "instock" if int(stock_qty) > 0 else "outofstock"
	else:
		product_data["stock_status"] = "instock"
	if sale_price is not None:
		product_data["sale_price"] = str(flt(sale_price))

	if getattr(item, "weight_per_unit", None):
		product_data["weight"] = str(flt(item.weight_per_unit))


	category_ids = _get_woo_category_ids(woo_item, api)
	if category_ids:
		product_data["categories"] = category_ids

	simple_attrs = _get_simple_item_attributes(item, woo_item, api)
	if simple_attrs:
		product_data["attributes"] = simple_attrs

	images = _get_item_images(item, woo_item, api)
	if images:
		product_data["images"] = images

	# Tags: prefer woo_tags Table MultiSelect, fallback to ERPNext _user_tags
	woo_tag_ids = _get_woo_tag_ids(woo_item, api)
	if woo_tag_ids:
		product_data["tags"] = woo_tag_ids
	elif getattr(item, "_user_tags", None):
		tags_list = [{"name": t.strip()} for t in item._user_tags.split(",") if t.strip()]
		if tags_list:
			product_data["tags"] = tags_list

	if woo_item.woo_product_id:
		result = api.update_product(woo_item.woo_product_id, product_data)
	else:
		existing_id = None
		if product_data.get("sku"):
			try:
				search_resp = api.get("products", params={"sku": product_data.get("sku"), "status": "any"})
				if search_resp.status_code == 200 and search_resp.json():
					existing_id = search_resp.json()[0].get("id")
			except Exception:
				frappe.log_error(title="woo_prime: SKU search error in _publish_simple_item_to_woo", message=frappe.get_traceback())

		if existing_id:
			woo_item.woo_product_id = existing_id
			result = api.update_product(existing_id, product_data)
		else:
			result = api.create_product(product_data)

	if result and result.get("id"):
		woo_item.woo_product_id = result.get("id")
		woo_item.published = 1
		permalink = result.get("permalink", "")
		woo_item.woo_product_url = html.unescape(permalink) if permalink else ""
		woo_item.sync_status = "Synced"
		woo_item.last_synced = now_datetime()
		woo_item.flags.in_woo_sync = True
		woo_item.save(ignore_permissions=True)

	return result


def _publish_template_item_to_woo(woo_item, item, api, settings):
	"""Publish an ERPNext Template Item and all its variants as a WooCommerce Variable Product using batch API."""
	import html
	variant_item_names = frappe.get_all(
		"Item",
		filters={"variant_of": item.name, "disabled": 0},
		pluck="name"
	)

	attributes_dict = {}
	variant_docs = {}
	for v_name in variant_item_names:
		v_item = frappe.get_doc("Item", v_name)
		variant_docs[v_name] = v_item
		for attr in v_item.attributes:
			if attr.attribute not in attributes_dict:
				attributes_dict[attr.attribute] = set()
			if attr.attribute_value:
				attributes_dict[attr.attribute].add(clean_text(attr.attribute_value))

	woo_attributes = []
	variation_attr_keys = set()

	if getattr(woo_item, "item_attributes", None) and len(woo_item.item_attributes) > 0:
		for idx, row in enumerate(woo_item.item_attributes):
			attr_name = row.attribute_name or row.attribute
			if not attr_name:
				continue
			opts = [o.strip() for o in (row.options or "").replace("\n", ",").split(",") if o.strip()]
			if not opts and row.attribute in attributes_dict:
				opts = sorted(list(attributes_dict[row.attribute]))

			# Skip variation attributes with no options — WooCommerce rejects empty options for variations
			if not opts and getattr(row, "is_variation", 1):
				continue

			woo_attr_id, target_name = _get_or_create_woo_attribute_id(attr_name, api)
			is_var = bool(getattr(row, "is_variation", 1))
			attr_payload = {
				"name": target_name or attr_name,
				"position": idx,  # Maintain order from child table
				"visible": bool(getattr(row, "is_visible", 1)),
				"variation": is_var,
				"options": opts if opts else [],
			}
			if woo_attr_id:
				attr_payload["id"] = int(woo_attr_id)
			woo_attributes.append(attr_payload)

			if is_var:
				variation_attr_keys.add(clean_text(attr_name).lower())
				if row.attribute:
					variation_attr_keys.add(clean_text(row.attribute).lower())
				if target_name:
					variation_attr_keys.add(clean_text(target_name).lower())
	else:
		# Fallback: auto-detected attributes (maintain alphabetical order as position)
		idx = 0
		for attr_name, options in sorted(attributes_dict.items()):
			woo_attr_id, target_name = _get_or_create_woo_attribute_id(attr_name, api)
			attr_payload = {
				"name": target_name or attr_name,
				"position": idx,
				"visible": True,
				"variation": True,
				"options": sorted(list(options))
			}
			if woo_attr_id:
				attr_payload["id"] = int(woo_attr_id)
			woo_attributes.append(attr_payload)
			variation_attr_keys.add(clean_text(attr_name).lower())
			if target_name:
				variation_attr_keys.add(clean_text(target_name).lower())
			idx += 1

	product_name = woo_item.item_name or item.item_name or ""
	product_data = {
		"name": html.unescape(product_name),
		"type": "variable",
		"sku": woo_item.sku,
		"description": html.unescape(woo_item.woo_description or item.description or ""),
		"short_description": html.unescape(getattr(woo_item, "woo_short_description", None) or item.description or ""),
		"status": "publish",
		"attributes": woo_attributes,
		"manage_stock": bool(getattr(woo_item, "manage_stock", 0)),
	}

	category_ids = _get_woo_category_ids(woo_item, api)
	if category_ids:
		product_data["categories"] = category_ids

	images = _get_item_images(item, woo_item, api)
	if images:
		product_data["images"] = images

	# Tags: prefer woo_tags Table MultiSelect, fallback to ERPNext _user_tags
	woo_tag_ids = _get_woo_tag_ids(woo_item, api)
	if woo_tag_ids:
		product_data["tags"] = woo_tag_ids
	elif getattr(item, "_user_tags", None):
		tags_list = [{"name": t.strip()} for t in item._user_tags.split(",") if t.strip()]
		if tags_list:
			product_data["tags"] = tags_list

	if woo_item.woo_product_id:
		result = api.update_product(woo_item.woo_product_id, product_data)
	else:
		# Check WooCommerce by SKU first to prevent creating duplicate products
		existing_id = None
		if product_data.get("sku"):
			try:
				search_resp = api.get("products", params={"sku": product_data.get("sku"), "status": "any"})
				if search_resp.status_code == 200 and search_resp.json():
					existing_id = search_resp.json()[0].get("id")
			except Exception:
				frappe.log_error(title="woo_prime: SKU search error in _publish_template_item_to_woo", message=frappe.get_traceback())

		if existing_id:
			woo_item.woo_product_id = existing_id
			result = api.update_product(existing_id, product_data)
		else:
			result = api.create_product(product_data)

	if not result or not result.get("id"):
		frappe.throw(f"WooCommerce did not return a valid product ID for template '{item.name}'. Response: {str(result)[:300]}")

	parent_woo_id = result.get("id")
	woo_item.woo_product_id = parent_woo_id
	woo_item.published = 1
	permalink = result.get("permalink", "")
	woo_item.woo_product_url = html.unescape(permalink) if permalink else ""
	woo_item.sync_status = "Synced"
	woo_item.last_synced = now_datetime()
	woo_item.flags.in_woo_sync = True
	woo_item.save(ignore_permissions=True)

	# --- BATCH VARIATIONS PUBLISH ---
	if variant_item_names:
		create_list = []
		update_list = []
		meta_list = []

		manage_var_stock = getattr(woo_item, "manage_variation_stock", None)
		for v_name in variant_item_names:
			v_item = variant_docs[v_name]
			child_woo_item = get_or_create_woo_item(v_name, v_item.item_code)
			if manage_var_stock is not None:
				child_woo_item.manage_stock = int(manage_var_stock)
				try:
					child_woo_item.db_set("manage_stock", int(manage_var_stock))
				except Exception:
					pass
			var_payload = _prepare_variation_payload(child_woo_item, v_item, api, settings, variation_attr_keys=variation_attr_keys)

			if child_woo_item.woo_product_id:
				var_payload["id"] = int(child_woo_item.woo_product_id)
				update_list.append(var_payload)
			else:
				create_list.append(var_payload)

			meta_list.append({
				"child_woo_item": child_woo_item,
				"sku": var_payload.get("sku"),
				"v_name": v_name
			})

		batch_payload = {}
		if create_list:
			batch_payload["create"] = create_list
		if update_list:
			batch_payload["update"] = update_list

		if batch_payload:
			try:
				batch_res = api.batch_product_variations(parent_woo_id, batch_payload)

				# Map results back to child_woo_item documents
				res_by_id = {}
				res_by_sku = {}

				create_results = batch_res.get("create", [])
				update_results = batch_res.get("update", [])

				for r in create_results + update_results:
					if isinstance(r, dict):
						if r.get("id"):
							res_by_id[r.get("id")] = r
						if r.get("sku"):
							res_by_sku[r.get("sku")] = r

				# Auto-recover variations that returned product_invalid_sku in batch
				if len(create_results) == len(create_list):
					for idx, r in enumerate(create_results):
						if isinstance(r, dict) and r.get("error"):
							err = r.get("error", {})
							if err.get("code") == "product_invalid_sku":
								resource_id = err.get("data", {}).get("resource_id")
								v_payload = create_list[idx]
								if resource_id:
									try:
										up_res = api._recover_and_update_variation(parent_woo_id, resource_id, v_payload)
										if up_res and up_res.get("id"):
											res_by_id[up_res.get("id")] = up_res
											if up_res.get("sku"):
												res_by_sku[up_res.get("sku")] = up_res
									except Exception:
										frappe.log_error(title="woo_prime: Batch variation SKU recovery failed", message=frappe.get_traceback())

				for meta in meta_list:
					child_woo_item = meta["child_woo_item"]
					sku = meta["sku"]

					r_data = None
					if child_woo_item.woo_product_id and int(child_woo_item.woo_product_id) in res_by_id:
						r_data = res_by_id[int(child_woo_item.woo_product_id)]
					elif sku and sku in res_by_sku:
						r_data = res_by_sku[sku]

					if r_data:
						child_woo_item.woo_product_id = r_data.get("id")
						child_woo_item.published = 1
						v_permalink = r_data.get("permalink", "")
						child_woo_item.woo_product_url = html.unescape(v_permalink) if v_permalink else ""
						child_woo_item.sync_status = "Synced"
						child_woo_item.last_synced = now_datetime()
						child_woo_item.flags.in_woo_sync = True
						child_woo_item.save(ignore_permissions=True)
			except Exception as e:
				frappe.log_error(f"Batch variations sync failed for template {item.name}: {e}")
				# Fallback to individual sync if batch fails
				for meta in meta_list:
					v_item = variant_docs[meta["v_name"]]
					_publish_variation_data(parent_woo_id, meta["child_woo_item"], v_item, api, settings)

	return result


def get_or_create_woo_item(item_code, sku=None):
	"""Get existing Woo Item by item_code, sku, or primary key name, or create a new one safely.

	Prevents IntegrityError (Duplicate entry for PRIMARY key).
	"""
	if not sku:
		sku = frappe.db.get_value("Item", item_code, "item_code") or item_code

	# 1. Lookup by item_code
	woo_item_name = frappe.db.get_value("Woo Item", {"item_code": item_code}, "name")

	# 2. Lookup by sku field
	if not woo_item_name and sku:
		woo_item_name = frappe.db.get_value("Woo Item", {"sku": sku}, "name")

	# 3. Lookup by primary key name (since Woo Item autoname is field:sku)
	if not woo_item_name and sku and frappe.db.exists("Woo Item", sku):
		woo_item_name = sku

	if woo_item_name:
		woo_item = frappe.get_doc("Woo Item", woo_item_name)
		changed = False
		if not woo_item.item_code and item_code:
			woo_item.item_code = item_code
			changed = True
		if not woo_item.sku and sku:
			woo_item.sku = sku
			changed = True
		if not woo_item.regular_price or flt(woo_item.regular_price) <= 0:
			settings = frappe.get_single("Woo Settings")
			price_list = getattr(settings, "default_price_list", None) or "Standard Selling"
			price = _get_item_price(item_code, price_list)
			if price:
				woo_item.regular_price = flt(price)
				changed = True
		if changed:
			woo_item.save(ignore_permissions=True)
		return woo_item

	# Create new Woo Item doc safely
	woo_item = frappe.new_doc("Woo Item")
	woo_item.item_code = item_code
	woo_item.sku = sku or item_code
	settings = frappe.get_single("Woo Settings")
	price_list = getattr(settings, "default_price_list", None) or "Standard Selling"
	price = _get_item_price(item_code, price_list)
	if price:
		woo_item.regular_price = flt(price)
	try:
		woo_item.insert(ignore_permissions=True)
	except Exception as e:
		target_name = sku or item_code
		if frappe.db.exists("Woo Item", target_name):
			woo_item = frappe.get_doc("Woo Item", target_name)
			changed = False
			if not woo_item.item_code and item_code:
				woo_item.item_code = item_code
				changed = True
			if not woo_item.regular_price or flt(woo_item.regular_price) <= 0:
				price = _get_item_price(item_code, price_list)
				if price:
					woo_item.regular_price = flt(price)
					changed = True
			if changed:
				woo_item.save(ignore_permissions=True)
		else:
			raise e

	return woo_item


def _publish_variant_item_to_woo(woo_item, item, api, settings):
	"""Publish a child Variant item directly by ensuring its parent template is published first."""
	parent_item_name = item.variant_of
	parent_item = frappe.get_doc("Item", parent_item_name)

	parent_woo_item = get_or_create_woo_item(parent_item_name, parent_item.item_code)

	if not parent_woo_item.woo_product_id:
		parent_res = _publish_template_item_to_woo(parent_woo_item, parent_item, api, settings)
		parent_woo_item.woo_product_id = parent_res.get("id")
		parent_woo_item.published = 1
		parent_woo_item.sync_status = "Synced"
		parent_woo_item.last_synced = now_datetime()
		parent_woo_item.save(ignore_permissions=True)

	return _publish_variation_data(parent_woo_item.woo_product_id, woo_item, item, api, settings)


def _prepare_variation_payload(child_woo_item, v_item, api, settings, variation_attr_keys=None):
	"""Prepare variation payload dictionary for WooCommerce."""
	import html
	price = _get_item_price(v_item.name, settings.default_price_list)
	regular_price = flt(child_woo_item.regular_price) if getattr(child_woo_item, "regular_price", None) and flt(child_woo_item.regular_price) > 0 else flt(price)
	if (not child_woo_item.regular_price or flt(child_woo_item.regular_price) <= 0) and regular_price > 0:
		child_woo_item.regular_price = flt(regular_price)
	sale_price = flt(child_woo_item.sale_price) if getattr(child_woo_item, "sale_price", None) and flt(child_woo_item.sale_price) > 0 else None
	stock_qty = _get_stock_qty(v_item.name, settings.default_warehouse)

	if variation_attr_keys is None and getattr(v_item, "variant_of", None):
		parent_woo = frappe.db.get_value("Woo Item", {"item_code": v_item.variant_of})
		if parent_woo:
			try:
				p_doc = frappe.get_doc("Woo Item", parent_woo)
				if getattr(p_doc, "item_attributes", None) and len(p_doc.item_attributes) > 0:
					variation_attr_keys = {
						clean_text(r.attribute_name or r.attribute).lower()
						for r in p_doc.item_attributes if getattr(r, "is_variation", 1)
					}
			except Exception:
				pass

	var_attributes = []
	for attr in v_item.attributes:
		attr_key = clean_text(attr.attribute).lower()
		woo_attr_id, target_name = _get_or_create_woo_attribute_id(attr.attribute, api)
		target_key = clean_text(target_name).lower() if target_name else attr_key
		# Bug #7 fix: also try slug/scrubbed forms to avoid missing valid variation attrs
		slug_key = frappe.scrub(attr.attribute).replace("_", "-").lower()
		pa_slug_key = "pa_" + slug_key

		# Filter out attributes not marked as variation
		if variation_attr_keys is not None and len(variation_attr_keys) > 0:
			candidate_keys = {attr_key, target_key, slug_key, pa_slug_key}
			if not candidate_keys.intersection(variation_attr_keys):
				continue

		var_attr = {
			"name": target_name or attr.attribute,
			"option": attr.attribute_value
		}
		if woo_attr_id:
			var_attr["id"] = int(woo_attr_id)
		var_attributes.append(var_attr)

	manage_stock = bool(getattr(child_woo_item, "manage_stock", 1))
	var_data = {
		"sku": child_woo_item.sku or v_item.item_code,
		"regular_price": str(flt(regular_price)),
		"manage_stock": manage_stock,
		"status": "publish",
		"attributes": var_attributes,
		"description": html.unescape(child_woo_item.woo_description or v_item.description or ""),
	}
	if manage_stock:
		var_data["stock_quantity"] = int(stock_qty)
		var_data["stock_status"] = "instock" if int(stock_qty) > 0 else "outofstock"
	else:
		var_data["stock_status"] = "instock"
	if sale_price is not None:
		var_data["sale_price"] = str(flt(sale_price))

	if getattr(v_item, "weight_per_unit", None):
		var_data["weight"] = str(flt(v_item.weight_per_unit))


	images = _get_item_images(v_item, child_woo_item, api)
	if images:
		var_data["image"] = images[0]

	return var_data


def _publish_variation_data(parent_woo_id, child_woo_item, v_item, api, settings):
	"""Publish a single variation to WooCommerce under parent variable product."""
	var_data = _prepare_variation_payload(child_woo_item, v_item, api, settings)

	if child_woo_item.woo_product_id:
		res = api.update_product_variation(parent_woo_id, child_woo_item.woo_product_id, var_data)
	else:
		res = api.create_product_variation(parent_woo_id, var_data)

	if res and res.get("id"):
		child_woo_item.woo_product_id = res.get("id")
		child_woo_item.published = 1
		permalink = res.get("permalink", "")
		child_woo_item.woo_product_url = html.unescape(permalink) if permalink else ""
		child_woo_item.sync_status = "Synced"
		child_woo_item.last_synced = now_datetime()
		child_woo_item.flags.in_woo_sync = True
		child_woo_item.save(ignore_permissions=True)
	return res


def _get_cached_wp_media_id(path):
	"""Check if an image file has already been uploaded to WordPress Media library."""
	if not path:
		return None
	cache_key = f"woo_wp_media_id:{path}"
	cached_id = frappe.cache().get_value(cache_key)
	if cached_id:
		return cached_id
	return None


def _cache_wp_media_id(path, media_id):
	"""Cache uploaded WordPress media ID for a file path."""
	if not path or not media_id:
		return
	cache_key = f"woo_wp_media_id:{path}"
	frappe.cache().set_value(cache_key, media_id)


def _get_item_images(item, woo_item=None, api=None):
	"""Get image payload for WooCommerce (attempts direct media upload to WordPress API first, with caching)."""
	images = []
	seen_paths = set()

	def process_image(path, caption=None):
		if not path or path in seen_paths:
			return
		seen_paths.add(path)

		is_private = path.startswith("/private/files/") or path.startswith("private/files/")

		# For private files, automatically copy to public for WooCommerce access
		if is_private:
			try:
				# Check if auto-copy is enabled
				settings = frappe.get_single("Woo Settings")
				auto_copy = getattr(settings, "auto_copy_private_images", 1)
				
				if not auto_copy:
					# Skip private images if auto-copy disabled
					frappe.log_error(
						title="Private Image Skipped",
						message=f"Private image {path} skipped. Enable 'Auto-Copy Private Images to Public' in Woo Settings to sync."
					)
					return
				
				import os
				import shutil
				
				# Get absolute paths
				clean_path = path.lstrip("/")
				private_path = frappe.get_site_path(clean_path)
				
				# Create public path (same filename in public/files/)
				filename = os.path.basename(private_path)
				public_path = frappe.get_site_path("public", "files", filename)
				
				# Check if file exists in private
				if os.path.exists(private_path):
					# Copy to public if not already there or if newer
					if not os.path.exists(public_path) or os.path.getmtime(private_path) > os.path.getmtime(public_path):
						# Create public/files directory if it doesn't exist
						os.makedirs(os.path.dirname(public_path), exist_ok=True)
						
						# Copy file to public
						shutil.copy2(private_path, public_path)
						
						frappe.logger("woo_prime").info(
							f"Auto-copied private image to public: {filename}"
						)
					
					# Use the public path for URL generation
					path = f"/files/{filename}"
					is_private = False
				else:
					frappe.log_error(
						title="Private Image File Not Found",
						message=f"Private image file does not exist: {private_path}"
					)
					return
					
			except Exception as e:
				frappe.log_error(
					title="Failed to Copy Private Image to Public",
					message=f"Error copying {path} to public: {str(e)}\n\n{frappe.get_traceback()}"
				)
				# Show user-friendly message
				if frappe.request:
					frappe.msgprint(
						f"⚠️ Could not prepare private image <b>{os.path.basename(path)}</b> for WooCommerce sync.<br>"
						"Please check Error Log for details.",
						indicator="orange",
						alert=True,
					)
				return

		# Generate public URL for WooCommerce
		if path.startswith("http://") or path.startswith("https://"):
			url = path
		else:
			url = frappe.utils.get_url(path)
		
		# Validate URL is publicly accessible
		if "localhost" in url or "127.0.0.1" in url:
			frappe.log_error(
				title="Image URL Not Publicly Accessible",
				message=f"Image URL contains localhost/127.0.0.1: {url}\n\n"
				        "WooCommerce server cannot reach this URL from the internet.\n"
				        "Please configure a public domain name in site_config.json:\n"
				        '  "host_name": "https://yourdomain.com"'
			)
			# Still send it - might work if same network/VPN
		
		img_dict = {"src": url}
		if caption:
			img_dict["alt"] = caption
		images.append(img_dict)

	# 1. Primary image (Woo Item image takes priority if manually uploaded, fallback to Item.image)
	primary_image = getattr(woo_item, "image", None) or item.image
	process_image(primary_image)

	# 2. Child Table images from Woo Item `images` table
	if woo_item and hasattr(woo_item, "images") and woo_item.images:
		for row in woo_item.images:
			img_path = getattr(row, "image", None)
			caption = getattr(row, "caption", None)
			process_image(img_path, caption)

	# 3. Additional attached images (Gallery) from File doctype
	attached_files = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Item",
			"attached_to_name": item.name,
		},
		fields=["file_url"],
		order_by="creation asc"
	)

	for f in attached_files:
		file_url = f.get("file_url")
		if not file_url:
			continue
		ext = file_url.lower().rsplit(".", 1)[-1] if "." in file_url else ""
		if ext in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
			process_image(file_url)

	return images


def _get_cat_cache():
	"""Request-scoped category cache — never stale across requests."""
	if not hasattr(frappe.local, "_woo_cat_cache"):
		frappe.local._woo_cat_cache = {}
	return frappe.local._woo_cat_cache


def _get_woo_category_ids(woo_item, api):
	"""Extract and sync WooCommerce category IDs for a Woo Item (supports multi-selection and parent-child hierarchy)."""
	category_ids = []
	seen_ids = set()

	def add_category_by_name(cat_name):
		if not cat_name:
			return

		# Recursively add parent category first to preserve hierarchy
		parent_cat = frappe.db.get_value("Woo Category", cat_name, "parent_woo_category")
		if parent_cat:
			add_category_by_name(parent_cat)

		woo_cat_id = frappe.db.get_value("Woo Category", cat_name, "woo_category_id")
		if not woo_cat_id:
			woo_cat_id = _get_or_create_woo_category_id(cat_name, api)

		if woo_cat_id and int(woo_cat_id) not in seen_ids:
			category_ids.append({"id": int(woo_cat_id)})
			seen_ids.add(int(woo_cat_id))

	# 1. Check Table MultiSelect `categories`
	if hasattr(woo_item, "categories") and woo_item.categories:
		for row in woo_item.categories:
			cat_name = getattr(row, "category", None)
			add_category_by_name(cat_name)

	# 2. Check primary `woo_category` field
	if getattr(woo_item, "woo_category", None):
		add_category_by_name(woo_item.woo_category)

	return category_ids


def _get_woo_tag_ids(woo_item, api):
	"""Extract and sync WooCommerce tag IDs from the woo_tags Table MultiSelect field.

	For each tag in woo_item.woo_tags:
	  1. Lookup woo_tag_id from Woo Tag DocType
	  2. If not found, search WooCommerce by name or create it
	  3. Return list of {id: <woo_tag_id>} dicts for the product payload
	"""
	tag_ids = []
	seen_ids = set()

	if not (hasattr(woo_item, "woo_tags") and woo_item.woo_tags):
		return tag_ids

	for row in woo_item.woo_tags:
		tag_name = getattr(row, "tag", None)
		if not tag_name:
			continue
		tag_name = tag_name.strip()
		if not tag_name:
			continue

		try:
			woo_tag_id = frappe.db.get_value("Woo Tag", tag_name, "woo_tag_id")

			if not woo_tag_id:
				# Search WooCommerce by name
				search_resp = api.get("products/tags", params={"search": tag_name, "per_page": 10})
				if search_resp.status_code == 200:
					for t in search_resp.json():
						if t.get("name", "").strip().lower() == tag_name.lower():
							woo_tag_id = int(t.get("id"))
							# Save back to DocType
							if frappe.db.exists("Woo Tag", tag_name):
								frappe.db.set_value("Woo Tag", tag_name, "woo_tag_id", woo_tag_id)
							break

			if not woo_tag_id:
				# Create new tag in WooCommerce
				slug = frappe.scrub(tag_name).replace("_", "-")
				create_resp = api.post("products/tags", data={"name": tag_name, "slug": slug})
				if create_resp.status_code in (200, 201):
					woo_tag_id = int(create_resp.json().get("id", 0))
					if woo_tag_id and frappe.db.exists("Woo Tag", tag_name):
						frappe.db.set_value("Woo Tag", tag_name, "woo_tag_id", woo_tag_id)

			if woo_tag_id and int(woo_tag_id) not in seen_ids:
				tag_ids.append({"id": int(woo_tag_id)})
				seen_ids.add(int(woo_tag_id))
		except Exception:
			frappe.log_error(
				title=f"woo_prime: Tag sync error for '{tag_name}'",
				message=frappe.get_traceback()
			)

	return tag_ids


def _get_or_create_woo_category_id(cat_name, api):
	"""Search or create WooCommerce category by name and return its WooCommerce ID (preserving parent-child link)."""
	if not cat_name:
		return None

	_cat_cache = _get_cat_cache()
	cat_key = cat_name.strip().lower()
	if cat_key in _cat_cache:
		return _cat_cache[cat_key]

	try:
		if frappe.db.exists("Woo Category", cat_name):
			woo_cat_id = frappe.db.get_value("Woo Category", cat_name, "woo_category_id")
			if woo_cat_id:
				_cat_cache[cat_key] = int(woo_cat_id)
				return int(woo_cat_id)

			from woo_prime.woo_prime.doctype.woo_category.woo_category import push_category_to_woo
			res = push_category_to_woo(cat_name)
			if res and isinstance(res, dict) and res.get("id"):
				cid = int(res.get("id"))
				_cat_cache[cat_key] = cid
				return cid

		response = api.get("products/categories", params={"search": cat_name})
		if response.status_code == 200:
			categories = response.json()
			for cat in categories:
				if cat.get("name", "").strip().lower() == cat_key:
					cid = int(cat.get("id"))
					_save_woo_category_loc(cat_name, cid, cat.get("slug"))
					_cat_cache[cat_key] = cid
					return cid

		create_resp = api.post("products/categories", data={"name": cat_name})
		if create_resp.status_code in (200, 201):
			new_cat = create_resp.json()
			cat_id = int(new_cat.get("id"))
			_save_woo_category_loc(cat_name, cat_id, new_cat.get("slug"))
			_cat_cache[cat_key] = cat_id
			return cat_id
	except Exception:
		frappe.log_error(title=f"woo_prime: Category creation/fetch error for {cat_name}", message=frappe.get_traceback())
def _get_attr_cache():
	"""Return a request-scoped attribute ID cache stored in frappe.local.
	Using frappe.local avoids stale data from a module-level dict that persists
	across requests in the same worker process (Bug #6).
	"""
	if not hasattr(frappe.local, "_woo_attr_cache"):
		frappe.local._woo_attr_cache = {}
	return frappe.local._woo_attr_cache


def _get_or_create_woo_attribute_id(attr_name, api=None):
	"""Get WooCommerce Global Attribute ID and mapped WooCommerce attribute name.
	Checks:
	1. Woo Settings attribute_mappings table (explicit user mapping)
	2. Woo Attribute DocType erpnext_item_attribute Link
	3. Woo Attribute DocType attribute_name or slug
	4. Direct WooCommerce API lookup

	Returns:
		tuple: (woo_attr_id: int or None, target_attr_name: str)
	"""
	if not attr_name:
		return (None, attr_name)

	key = attr_name.strip().lower()
	# Bug #6 fix: use request-scoped cache instead of stale module-level dict
	_cache = _get_attr_cache()
	if key in _cache:
		return _cache[key]

	# 1. Check Woo Settings attribute_mappings table (explicit user mapping)
	try:
		settings = frappe.get_single("Woo Settings")
		if getattr(settings, "attribute_mappings", None):
			for row in settings.attribute_mappings:
				if (row.erpnext_attribute or "").strip().lower() == key:
					woo_attr_id = row.woo_attribute_id or frappe.db.get_value("Woo Attribute", row.woo_attribute, "woo_attribute_id")
					woo_attr_name = frappe.db.get_value("Woo Attribute", row.woo_attribute, "attribute_name") or row.woo_attribute
					if woo_attr_id:
						res = (int(woo_attr_id), woo_attr_name)
						_cache[key] = res
						return res
	except Exception:
		pass

	# 2. Check local Woo Attribute DocType
	if frappe.db.exists("DocType", "Woo Attribute"):
		matched = frappe.db.get_value("Woo Attribute", {"erpnext_item_attribute": attr_name}, ["woo_attribute_id", "attribute_name"], as_dict=True)
		if not matched:
			matched = frappe.db.get_value("Woo Attribute", {"attribute_name": attr_name}, ["woo_attribute_id", "attribute_name"], as_dict=True)
		if not matched:
			clean_slug = frappe.scrub(attr_name).replace("_", "-")
			matched = frappe.db.get_value("Woo Attribute", {"slug": clean_slug}, ["woo_attribute_id", "attribute_name"], as_dict=True)

		if matched and matched.get("woo_attribute_id"):
			res = (int(matched.woo_attribute_id), matched.attribute_name)
			_cache[key] = res
			return res

	# 3. Query WooCommerce API
	if api:
		try:
			res = api.get("products/attributes")
			if res.status_code == 200 and isinstance(res.json(), list):
				for a in res.json():
					a_name = (a.get("name") or "").strip()
					a_slug = (a.get("slug") or "").strip().lower()
					clean_slug = a_slug.replace("pa_", "")
					aid = int(a.get("id"))
					entry = (aid, a_name)
					_cache[a_name.lower()] = entry
					_cache[a_slug] = entry
					_cache[clean_slug] = entry

					# Save to Woo Attribute if DocType exists
					if frappe.db.exists("DocType", "Woo Attribute") and not frappe.db.exists("Woo Attribute", {"woo_attribute_id": aid}):
						try:
							wa = frappe.new_doc("Woo Attribute")
							wa.attribute_name = a.get("name")
							wa.woo_attribute_id = aid
							wa.slug = a.get("slug")
							wa.type = a.get("type", "select")
							wa.save(ignore_permissions=True)
						except Exception:
							pass

				if key in _cache:
					return _cache[key]
		except Exception:
			safe_log_error(title=f"woo_prime: Attribute ID lookup error for {attr_name}", message=frappe.get_traceback())

	# 4. If still not found anywhere, automatically CREATE the Global Attribute on WooCommerce!
	if api:
		try:
			create_payload = {
				"name": attr_name,
				"slug": frappe.scrub(attr_name).replace("_", "-"),
				"type": "select",
				"order_by": "menu_order",
				"has_archives": True
			}
			create_res = api.post("products/attributes", data=create_payload)
			if create_res.status_code in (200, 201):
				created_data = create_res.json()
				new_id = int(created_data.get("id"))
				new_name = created_data.get("name") or attr_name
				new_slug = (created_data.get("slug") or "").strip().lower()
				clean_slug = new_slug.replace("pa_", "")
				res = (new_id, new_name)
				_cache[key] = res
				_cache[new_name.lower()] = res
				_cache[new_slug] = res
				_cache[clean_slug] = res

				# Auto-save to Woo Attribute DocType
				if frappe.db.exists("DocType", "Woo Attribute"):
					try:
						wa = frappe.new_doc("Woo Attribute")
						wa.attribute_name = new_name
						wa.woo_attribute_id = new_id
						wa.slug = new_slug
						wa.type = "select"
						wa.erpnext_item_attribute = attr_name
						wa.save(ignore_permissions=True)
					except Exception:
						pass

				return res
		except Exception as e:
			frappe.logger("woo_prime").error(f"Failed to auto-create global attribute '{attr_name}' on WooCommerce: {e}")

	return (None, attr_name)


def _get_simple_item_attributes(item, woo_item=None, api=None):
	"""Extract Item Attributes for simple products. Uses woo_item.item_attributes if available, else item.attributes."""
	if woo_item and getattr(woo_item, "item_attributes", None) and len(woo_item.item_attributes) > 0:
		woo_attrs = []
		for idx, row in enumerate(woo_item.item_attributes):
			attr_name = row.attribute_name or row.attribute
			if not attr_name:
				continue
			opts = [o.strip() for o in (row.options or "").replace("\n", ",").split(",") if o.strip()]
			woo_attr_id, target_name = _get_or_create_woo_attribute_id(attr_name, api)
			attr_payload = {
				"name": target_name or attr_name,
				"position": idx,  # Maintain order from child table
				"visible": bool(getattr(row, "is_visible", 1)),
				"variation": False,
				"options": opts if opts else [],
			}
			if woo_attr_id:
				attr_payload["id"] = int(woo_attr_id)
			woo_attrs.append(attr_payload)
		return woo_attrs

	if not hasattr(item, "attributes") or not item.attributes:
		return []

	woo_attrs = []
	for idx, attr in enumerate(item.attributes):
		if attr.attribute and attr.attribute_value:
			woo_attr_id, target_name = _get_or_create_woo_attribute_id(attr.attribute, api)
			attr_payload = {
				"name": target_name or attr.attribute,
				"position": idx,
				"visible": True,
				"variation": False,
				"options": [attr.attribute_value]
			}
			if woo_attr_id:
				attr_payload["id"] = int(woo_attr_id)
			woo_attrs.append(attr_payload)
	return woo_attrs


def _save_woo_category_loc(cat_name, woo_cat_id, slug=None):
	"""Helper to save/update Woo Category record in ERPNext."""
	try:
		if not frappe.db.exists("Woo Category", cat_name):
			doc = frappe.new_doc("Woo Category")
			doc.category_name = cat_name
			doc.woo_category_id = woo_cat_id
			doc.slug = slug
			doc.insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Woo Category", cat_name, "woo_category_id", woo_cat_id)
	except Exception:
		frappe.log_error(title=f"woo_prime: _save_woo_category_loc error for {cat_name}", message=frappe.get_traceback())



def sync_stock_to_woo(woo_item):
	"""Push current ERPNext stock to WooCommerce.

	Args:
		woo_item: Woo Item document
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	api = get_woo_api()
	settings = frappe.get_single("Woo Settings")

	stock_qty = _get_stock_qty(woo_item.item_code, settings.default_warehouse)

	manage_stock = bool(getattr(woo_item, "manage_stock", 1))

	# Check if item is a Variant that should be synced as a variation
	# Respect woo_product_type: "Simple Item" means standalone product, not variation
	variant_of = frappe.db.get_value("Item", woo_item.item_code, "variant_of") if woo_item.item_code else None
	parent_woo_id = None
	woo_product_type = getattr(woo_item, "woo_product_type", "")
	
	# Only treat as variation if variant_of exists AND woo_product_type is NOT "Simple Item" or "Variant Item"
	if variant_of and woo_product_type not in ("Simple Item", "Variant Item"):
		parent_woo_id = frappe.db.get_value("Woo Item", {"item_code": variant_of}, "woo_product_id")

	try:
		if parent_woo_id:
			# Sync as variation under parent product
			api.update_variation_stock(parent_woo_id, woo_item.woo_product_id, stock_qty, manage_stock=manage_stock)
		else:
			# Sync as standalone product (simple or variable)
			api.update_stock(woo_item.woo_product_id, stock_qty, manage_stock=manage_stock)
	except frappe.ValidationError as e:
		# 404 — product gone from WooCommerce or wrong sync method (variation vs product)
		err_msg = str(e)
		if "404" in err_msg or "not found" in err_msg.lower():
			# Mark as unpublished
			woo_item.published = 0
			woo_item.db_set("published", 0)
			
			# Provide helpful message
			if parent_woo_id:
				frappe.msgprint(
					f"⚠️ WooCommerce variation #{woo_item.woo_product_id} (parent #{parent_woo_id}) not found (404) — marked as unpublished.<br><br>"
					f"<b>Possible reasons:</b><br>"
					f"• Variation was deleted from WooCommerce<br>"
					f"• Product type mismatch (published as Simple but trying to sync as Variation)<br><br>"
					f"<b>Solution:</b> Click 'Publish to WooCommerce' to recreate the product.",
					indicator="orange",
					title="Stock Sync Failed"
				)
			else:
				frappe.msgprint(
					f"⚠️ WooCommerce product #{woo_item.woo_product_id} not found (404) — marked as unpublished.<br><br>"
					f"<b>Solution:</b> Click 'Publish to WooCommerce' to recreate the product.",
					indicator="orange",
					title="Stock Sync Failed"
				)
		
		woo_item.sync_status = "Error"
		woo_item.db_set("sync_status", "Error")
		from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
		create_log(
			sync_type="Stock",
			direction="Outgoing",
			status="Failed",
			reference_doctype="Woo Item",
			reference_name=woo_item.name,
			woo_reference_id=str(woo_item.woo_product_id),
			request_data={"item_code": woo_item.item_code, "stock_quantity": stock_qty, "warehouse": settings.default_warehouse},
			response_data={"error": err_msg},
			error_message=err_msg,
		)
		frappe.logger("woo_prime").error(f"[sync_stock_to_woo] 404/error for {woo_item.name}: {err_msg}")
		return

	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	woo_item.db_set("last_pushed_qty", int(stock_qty))

	create_log(
		sync_type="Stock",
		direction="Outgoing",
		status="Success",
		reference_doctype="Woo Item",
		reference_name=woo_item.name,
		woo_reference_id=str(woo_item.woo_product_id),
		request_data={"item_code": woo_item.item_code, "stock_quantity": stock_qty, "warehouse": settings.default_warehouse},
		response_data={"stock_quantity": stock_qty, "status": "Success"},
	)


def sync_price_to_woo(woo_item):
	"""Push current ERPNext price to WooCommerce.

	Args:
		woo_item: Woo Item document
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	api = get_woo_api()
	settings = frappe.get_single("Woo Settings")

	price = _get_item_price(woo_item.item_code, settings.default_price_list)
	regular_price = flt(price) if price > 0 else (flt(woo_item.regular_price) if getattr(woo_item, "regular_price", None) else 0.0)

	# Guard: never send price 0 when Woo Item has no regular_price configured
	if regular_price <= 0:
		if not (getattr(woo_item, "regular_price", None) and flt(woo_item.regular_price) > 0):
			frappe.logger("woo_prime").warning(
				f"[sync_price_to_woo] Skipping {woo_item.name} — resolved price is 0"
			)
			return

	if regular_price > 0 and (not woo_item.regular_price or flt(woo_item.regular_price) != regular_price):
		woo_item.db_set("regular_price", regular_price)

	# Determine sale_price: clear when empty/0, else send the value
	raw_sale = getattr(woo_item, "sale_price", None)
	sale_price = flt(raw_sale) if raw_sale else 0.0
	clear_sale = sale_price <= 0  # True → send sale_price="" to WooCommerce

	# Check if item is a Variant that should be synced as a variation
	# Respect woo_product_type: "Simple Item" means standalone product, not variation
	variant_of = frappe.db.get_value("Item", woo_item.item_code, "variant_of") if woo_item.item_code else None
	parent_woo_id = None
	woo_product_type = getattr(woo_item, "woo_product_type", "")
	
	# Only treat as variation if variant_of exists AND woo_product_type is NOT "Simple Item" or "Variant Item"
	if variant_of and woo_product_type not in ("Simple Item", "Variant Item"):
		parent_woo_id = (
			frappe.db.get_value("Woo Item", {"item_code": variant_of}, "woo_product_id")
			or frappe.db.get_value("Woo Item", {"sku": variant_of}, "woo_product_id")
			or (frappe.db.exists("Woo Item", variant_of) and frappe.db.get_value("Woo Item", variant_of, "woo_product_id"))
		)

	try:
		if parent_woo_id:
			api.update_variation_price(
				parent_woo_id, woo_item.woo_product_id, regular_price,
				sale_price=flt(sale_price) if not clear_sale else None,
				clear_sale=clear_sale,
			)
		else:
			api.update_price(
				woo_item.woo_product_id, regular_price,
				sale_price=flt(sale_price) if not clear_sale else None,
				clear_sale=clear_sale,
			)
	except frappe.ValidationError as e:
		err_msg = str(e)
		if "404" in err_msg or "not found" in err_msg.lower():
			woo_item.published = 0
			woo_item.db_set("published", 0)
		woo_item.sync_status = "Error"
		woo_item.db_set("sync_status", "Error")
		from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
		create_log(
			sync_type="Price",
			direction="Outgoing",
			status="Failed",
			reference_doctype="Woo Item",
			reference_name=woo_item.name,
			woo_reference_id=str(woo_item.woo_product_id),
			request_data={"item_code": woo_item.item_code, "regular_price": regular_price, "sale_price": sale_price if not clear_sale else ""},
			response_data={"error": err_msg},
			error_message=err_msg,
		)
		frappe.logger("woo_prime").error(f"[sync_price_to_woo] 404/error for {woo_item.name}: {err_msg}")
		return

	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	woo_item.db_set("last_pushed_price", flt(regular_price))

	create_log(
		sync_type="Price",
		direction="Outgoing",
		status="Success",
		reference_doctype="Woo Item",
		reference_name=woo_item.name,
		woo_reference_id=str(woo_item.woo_product_id),
		request_data={"item_code": woo_item.item_code, "regular_price": regular_price, "sale_price": sale_price if not clear_sale else ""},
		response_data={"regular_price": regular_price, "sale_price": sale_price if not clear_sale else "", "status": "Success"},
	)



def _get_item_price(item_code, price_list=None):
	"""Get item price from ERPNext price list.
	For variant items, if no Item Price exists for the variant item_code,
	falls back to the template item (variant_of)'s Item Price.
	If still not found, falls back to standard_rate on the Item or template Item.

	Args:
		item_code: str
		price_list: str (optional)

	Returns:
		float: price rate
	"""
	if not item_code:
		return 0.0

	if not price_list:
		settings = frappe.get_single("Woo Settings")
		price_list = getattr(settings, "default_price_list", None) or "Standard Selling"

	price = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": price_list, "selling": 1},
		"price_list_rate",
	)

	variant_of = None
	if not price:
		# Check if this item is a variant of a template item
		variant_of = frappe.db.get_value("Item", item_code, "variant_of")
		if variant_of:
			price = frappe.db.get_value(
				"Item Price",
				{"item_code": variant_of, "price_list": price_list, "selling": 1},
				"price_list_rate",
			)

	if not price:
		# Fallback to any selling price list if default price list had no rate
		price = frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "selling": 1},
			"price_list_rate",
		)
		if not price and variant_of:
			price = frappe.db.get_value(
				"Item Price",
				{"item_code": variant_of, "selling": 1},
				"price_list_rate",
			)

	if not price:
		# Fallback to standard_rate on Item doctype
		price = frappe.db.get_value("Item", item_code, "standard_rate")
		if not price and variant_of:
			price = frappe.db.get_value("Item", variant_of, "standard_rate")

	return flt(price)


def _get_stock_qty(item_code, warehouse):
	"""Get current stock quantity from ERPNext Bin (actual_qty - reserved_qty), floored at 0.

	Uses Bin directly to avoid permission checks that fail in guest/webhook context.

	Args:
		item_code: str
		warehouse: str

	Returns:
		int: available stock quantity (>= 0)
	"""
	if not item_code:
		return 0

	if warehouse:
		result = frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": warehouse},
			["actual_qty", "reserved_qty"],
			as_dict=True,
		)
		if result:
			qty = flt(result.actual_qty) - flt(result.reserved_qty)
			return max(0, int(qty))
		return 0
	else:
		# Sum across all warehouses
		rows = frappe.db.get_all(
			"Bin",
			filters={"item_code": item_code},
			fields=["actual_qty", "reserved_qty"],
		)
		total = sum(flt(r.actual_qty) - flt(r.reserved_qty) for r in rows)
		return max(0, int(total))


# ═══════════════════════════════════════════════════
# Scheduled Tasks
# ═══════════════════════════════════════════════════


STOCK_INTERVAL_MINUTES = {
	"Every 15 Minutes": 15,
	"Every 30 Minutes": 30,
	"Hourly": 60,
	"Every 6 Hours": 360,
	"Daily": 1440,
}

PRICE_INTERVAL_MINUTES = {
	"Hourly": 60,
	"Every 6 Hours": 360,
	"Daily": 1440,
}


def dynamic_scheduler_runner():
	"""Master scheduler runner (kept for backwards compatibility)."""
	scheduled_order_sync()
	scheduled_stock_sync()
	scheduled_price_sync()
	scheduled_item_sync()


def scheduled_order_sync():
	"""Scheduled job: checks interval and enqueues order sync."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not getattr(settings, "enable_order_sync", 1):
			return
		from woo_prime.woo_prime.doctype.woo_item.woo_item import _is_within_sync_window
		if not _is_within_sync_window():
			return
		frappe.enqueue(
			"woo_prime.api.sync.auto_sync_orders",
			queue="long",
			timeout=3000,
			job_id="woo_scheduled_order_sync",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(title="Scheduled Order Sync Dispatch Error", message=str(e))


def scheduled_stock_sync():
	"""Scheduled job: checks interval and enqueues stock sync."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not getattr(settings, "enable_stock_sync", 1):
			return
		from woo_prime.woo_prime.doctype.woo_item.woo_item import _is_within_sync_window
		if not _is_within_sync_window():
			return
		frappe.enqueue(
			"woo_prime.api.sync.sync_all_stock",
			queue="long",
			timeout=3000,
			job_id="woo_scheduled_stock_sync",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(title="Scheduled Stock Sync Dispatch Error", message=str(e))


def scheduled_price_sync():
	"""Scheduled job: checks interval and enqueues price sync."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not getattr(settings, "enable_price_sync", 1):
			return
		from woo_prime.woo_prime.doctype.woo_item.woo_item import _is_within_sync_window
		if not _is_within_sync_window():
			return
		frappe.enqueue(
			"woo_prime.api.sync.sync_all_prices",
			queue="long",
			timeout=3000,
			job_id="woo_scheduled_price_sync",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(title="Scheduled Price Sync Dispatch Error", message=str(e))


def scheduled_item_sync():
	"""Scheduled job: checks interval and enqueues item sync."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not getattr(settings, "enable_item_sync", 1):
			return
		from woo_prime.woo_prime.doctype.woo_item.woo_item import _is_within_sync_window
		if not _is_within_sync_window():
			return
		frappe.enqueue(
			"woo_prime.api.sync.sync_all_items",
			queue="long",
			timeout=3000,
			job_id="woo_scheduled_item_sync",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(title="Scheduled Item Sync Dispatch Error", message=str(e))


def sync_all_stock(force=False):
	"""Scheduled task: Push all Woo Item stock to WooCommerce based on stock_sync_interval in chunks of 100."""
	if isinstance(force, str):
		force = force.lower() in ("true", "1")

	settings = frappe.get_single("Woo Settings")
	if not settings.enabled or not getattr(settings, "enable_stock_sync", 1):
		return

	# Dynamic interval check from Woo Settings
	interval_str = getattr(settings, "stock_sync_interval", "Hourly") or "Hourly"
	interval_minutes = STOCK_INTERVAL_MINUTES.get(interval_str, 60)
	last_sync = getattr(settings, "last_stock_sync_time", None)

	if not force and last_sync:
		from frappe.utils import get_datetime
		elapsed_minutes = (now_datetime() - get_datetime(last_sync)).total_seconds() / 60.0
		if elapsed_minutes < interval_minutes:
			return

	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	api = get_woo_api()

	woo_items = frappe.get_all(
		"Woo Item",
		filters={"published": 1, "sync_stock": 1, "woo_product_id": [">", 0]},
		fields=["name", "item_code", "woo_product_id", "last_pushed_qty"],
		order_by="last_synced asc, creation asc",
	)

	chunk_size = 100
	for i in range(0, len(woo_items), chunk_size):
		chunk = woo_items[i : i + chunk_size]
		batch_updates = []
		batch_docs = []

		for item_data in chunk:
			try:
				qty = _get_stock_qty(item_data.item_code, settings.default_warehouse)
				last_qty = item_data.get("last_pushed_qty")
				if last_qty is not None and int(flt(last_qty)) == int(qty):
					continue

				variant_of = frappe.db.get_value("Item", item_data.item_code, "variant_of")
				parent_woo_id = None
				if variant_of:
					parent_woo_id = frappe.db.get_value("Woo Item", {"item_code": variant_of}, "woo_product_id")

				if parent_woo_id:
					woo_item = frappe.get_doc("Woo Item", item_data.name)
					sync_stock_to_woo(woo_item)
				else:
					manage_stock = bool(getattr(item_data, "manage_stock", 1))
					upd = {
						"id": int(item_data.woo_product_id),
						"manage_stock": manage_stock,
					}
					if manage_stock:
						upd["stock_quantity"] = int(qty)
						upd["stock_status"] = "instock" if int(qty) > 0 else "outofstock"
					else:
						upd["stock_status"] = "instock"
					batch_updates.append(upd)
					batch_docs.append((item_data.name, int(qty)))
			except Exception:
				frappe.log_error(f"Stock sync failed for Woo Item: {item_data.name}")

		if batch_updates:
			try:
				api.batch_products({"update": batch_updates})
				for doc_name, pushed_qty in batch_docs:
					frappe.db.set_value(
						"Woo Item", doc_name,
						{
							"last_pushed_qty": pushed_qty,
							"last_synced": now_datetime(),
							"sync_status": "Synced",
						},
						update_modified=False,
					)
			except Exception as e:
				frappe.log_error(f"Batch stock sync failed: {e}")
				for doc_name, _ in batch_docs:
					try:
						doc = frappe.get_doc("Woo Item", doc_name)
						sync_stock_to_woo(doc)
					except Exception:
						pass

		# Commit and save checkpoint after each chunk of 100
		frappe.db.set_value("Woo Settings", "Woo Settings", "last_stock_sync_time", now_datetime())
		frappe.db.commit()


def sync_all_prices(force=False):
	"""Scheduled task: Push all Woo Item prices to WooCommerce based on price_sync_interval in chunks of 100."""
	if isinstance(force, str):
		force = force.lower() in ("true", "1")

	settings = frappe.get_single("Woo Settings")
	if not settings.enabled or not getattr(settings, "enable_price_sync", 1):
		return

	# Dynamic interval check from Woo Settings
	interval_str = getattr(settings, "price_sync_interval", "Daily") or "Daily"
	interval_minutes = PRICE_INTERVAL_MINUTES.get(interval_str, 1440)
	last_sync = getattr(settings, "last_price_sync_time", None)

	if not force and last_sync:
		from frappe.utils import get_datetime
		elapsed_minutes = (now_datetime() - get_datetime(last_sync)).total_seconds() / 60.0
		if elapsed_minutes < interval_minutes:
			return

	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	api = get_woo_api()

	woo_items = frappe.get_all(
		"Woo Item",
		filters={"published": 1, "sync_price": 1, "woo_product_id": [">", 0]},
		fields=["name", "item_code", "woo_product_id", "regular_price", "sale_price", "last_pushed_price"],
		order_by="last_synced asc, creation asc",
	)

	chunk_size = 100
	for i in range(0, len(woo_items), chunk_size):
		chunk = woo_items[i : i + chunk_size]
		batch_updates = []
		batch_docs = []

		for item_data in chunk:
			try:
				price = _get_item_price(item_data.item_code, settings.default_price_list)
				regular_price = flt(price) if price > 0 else (flt(item_data.regular_price) if item_data.get("regular_price") else 0.0)
				if regular_price <= 0:
					continue

				last_price = item_data.get("last_pushed_price")
				if last_price is not None and abs(flt(last_price) - flt(regular_price)) < 0.001:
					continue

				raw_sale = item_data.get("sale_price")
				sale_price = flt(raw_sale) if raw_sale and flt(raw_sale) > 0 else None

				variant_of = frappe.db.get_value("Item", item_data.item_code, "variant_of")
				parent_woo_id = None
				if variant_of:
					parent_woo_id = (
						frappe.db.get_value("Woo Item", {"item_code": variant_of}, "woo_product_id")
						or frappe.db.get_value("Woo Item", {"sku": variant_of}, "woo_product_id")
					)

				if parent_woo_id:
					woo_item = frappe.get_doc("Woo Item", item_data.name)
					sync_price_to_woo(woo_item)
				else:
					p_data = {
						"id": int(item_data.woo_product_id),
						"regular_price": str(regular_price),
					}
					if sale_price is not None:
						p_data["sale_price"] = str(sale_price)
					else:
						p_data["sale_price"] = ""
					batch_updates.append(p_data)
					batch_docs.append((item_data.name, flt(regular_price)))
			except Exception:
				frappe.log_error(f"Price sync failed for Woo Item: {item_data.name}")

		if batch_updates:
			try:
				api.batch_products({"update": batch_updates})
				for doc_name, pushed_price in batch_docs:
					frappe.db.set_value(
						"Woo Item", doc_name,
						{
							"last_pushed_price": pushed_price,
							"regular_price": pushed_price,
							"last_synced": now_datetime(),
							"sync_status": "Synced",
						},
						update_modified=False,
					)
			except Exception as e:
				frappe.log_error(f"Batch price sync failed: {e}")
				for doc_name, _ in batch_docs:
					try:
						doc = frappe.get_doc("Woo Item", doc_name)
						sync_price_to_woo(doc)
					except Exception:
						pass

		# Commit and save checkpoint after each chunk of 100
		frappe.db.set_value("Woo Settings", "Woo Settings", "last_price_sync_time", now_datetime())
		frappe.db.commit()


def _get_all_woo_orders(api, params=None):
	"""Fetch all orders from WooCommerce handling pagination across multiple pages."""
	all_orders = []
	req_params = dict(params) if params else {}
	req_params["per_page"] = req_params.get("per_page", 100)
	page = 1

	while True:
		req_params["page"] = page
		batch = api.get_orders(params=req_params)
		if not batch:
			break
		all_orders.extend(batch)
		if len(batch) < req_params["per_page"]:
			break
		page += 1
		if page > 50:  # Safety cap
			break

	return all_orders


def reconcile_orders():
	"""Scheduled task: Reconcile WooCommerce orders with ERPNext.

	Fetches recent orders from WooCommerce and creates any missing Sales Orders.
	Runs daily to catch any missed webhook deliveries.
	"""
	settings = frappe.get_single("Woo Settings")
	if not settings.enabled:
		return

	try:
		from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

		api = get_woo_api()

		# Fetch orders from last configured reconcile_days (default 2)
		import datetime

		lookback_days = getattr(settings, "reconcile_days", 2) or 2
		after_date = (datetime.datetime.now() - datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%dT00:00:00")

		orders = _get_all_woo_orders(api, params={
			"after": after_date,
			"per_page": 100,
			"status": getattr(settings, "sync_order_statuses", None) or "processing,completed",
		})

		if not orders:
			return

		synced_count = 0
		for order in orders:
			woo_order_id = order.get("id")
			if not frappe.db.exists("Sales Order", {"woo_order_id": str(woo_order_id)}):
				try:
					sync_order(order)
					synced_count += 1
				except Exception:
					frappe.log_error(f"Reconciliation failed for WC Order: {woo_order_id}")

		if synced_count:
			frappe.log_error(
				title="WooCommerce Order Reconciliation",
				message=f"Reconciled {synced_count} missed orders",
			)

	except Exception:
		frappe.log_error(
			title="WooCommerce Reconciliation Failed",
			message=frappe.get_traceback(),
		)


@frappe.whitelist()
def auto_sync_orders(force=False):
	"""Fetch pending/processing orders from WooCommerce according to configured sync interval in minutes.

	- Checks enable_order_sync and order_sync_interval (in minutes) from Woo Settings.
	- Fetches WooCommerce orders, converts them to Sales Orders in ERPNext.
	- Can also be triggered manually (force=True).
	"""
	frappe.only_for(["System Manager", "Woo Manager"])
	if isinstance(force, str):
		force = force.lower() in ("true", "1")

	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled:
			return {"synced": 0, "message": "WooCommerce integration is disabled"}

		enable_order_sync = getattr(settings, "enable_order_sync", 1)
		if not enable_order_sync and not force:
			return {"synced": 0, "message": "Automatic Order Sync is disabled"}

		interval_minutes = int(getattr(settings, "order_sync_interval", 5) or 5)
		last_sync = getattr(settings, "last_order_sync_time", None)

		if not force and last_sync:
			from frappe.utils import get_datetime, now_datetime
			last_sync_dt = get_datetime(last_sync)
			elapsed_minutes = (now_datetime() - last_sync_dt).total_seconds() / 60.0
			if elapsed_minutes < interval_minutes:
				return {"synced": 0, "message": f"Interval of {interval_minutes}m has not elapsed yet"}

		from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
		api = get_woo_api()

		# Build query params with after=<last_order_sync_time - 1 day>
		import datetime
		lookback_days = int(getattr(settings, "reconcile_days", 2) or 2)
		if last_sync:
			from frappe.utils import get_datetime
			last_sync_dt = get_datetime(last_sync)
			after_dt = (last_sync_dt - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
		else:
			after_dt = (datetime.datetime.now() - datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%dT00:00:00")

		query_params = {
			"per_page": 100,
			"status": getattr(settings, "sync_order_statuses", None) or "processing,completed",
			"after": after_dt,
			"orderby": "date",
			"order": "asc",
		}

		# Get recent orders from WooCommerce (raises on non-200)
		orders = _get_all_woo_orders(api, params=query_params)

		# Only update last_order_sync_time if the API call succeeded
		frappe.db.set_value("Woo Settings", "Woo Settings", "last_order_sync_time", frappe.utils.now_datetime())
		frappe.db.commit()

		if not orders:
			return {"synced": 0, "message": "No new orders found on WooCommerce"}

		synced_count = 0
		for order in orders:
			woo_order_id = order.get("id")
			if not woo_order_id:
				continue

			if not frappe.db.exists("Sales Order", {"woo_order_id": str(woo_order_id)}):
				try:
					sync_order(order)
					synced_count += 1
				except Exception as err:
					frappe.log_error(title=f"Auto Order Sync Error (WC #{woo_order_id})", message=str(err))

		msg = f"Synced {synced_count} new orders from WooCommerce"
		if force:
			frappe.msgprint(_("✅ Successfully synced <b>{0}</b> orders from WooCommerce!").format(synced_count), indicator="green")

		return {"synced": synced_count, "message": msg}

	except Exception as e:
		frappe.log_error(title="Auto Order Sync Exception", message=frappe.get_traceback())
		if force:
			frappe.throw(_("Failed to sync orders from WooCommerce: {0}").format(str(e)))
		return {"synced": 0, "error": str(e)}


def prune_sync_logs():
	"""Daily scheduled job: Prune Woo Sync Log older than log_retention_days (default 30)."""
	try:
		settings = frappe.get_single("Woo Settings")
		retention_days = int(getattr(settings, "log_retention_days", 30) or 30)
		cutoff = add_days(today(), -retention_days)

		frappe.db.delete("Woo Sync Log", {
			"creation": ["<", cutoff]
		})
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(title="Prune Woo Sync Logs Failed", message=str(e))


# ═══════════════════════════════════════════════════
# Real-Time Event Hooks (Bin & Item Price)
# ═══════════════════════════════════════════════════


def on_bin_update(doc, method):
	"""Real-time event hook: Triggered whenever stock level (Bin) changes in ERPNext."""
	if not doc.item_code:
		return

	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not settings.enable_stock_sync:
			return

		# Check warehouse match if default warehouse configured
		if settings.default_warehouse and doc.warehouse != settings.default_warehouse:
			return

		frappe.enqueue(
			"woo_prime.api.sync.sync_stock_for_item_code",
			item_code=doc.item_code,
			enqueue_after_commit=True,
			job_id=f"woo_stock_{doc.item_code}",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(title="Real-Time Bin Hook Exception", message=str(e))


def sync_stock_for_item_code(item_code):
	"""Background worker: Push live updated stock qty for a specific item to WooCommerce."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not settings.enable_stock_sync:
			return

		woo_items = frappe.get_all(
			"Woo Item",
			filters={"published": 1, "sync_stock": 1, "woo_product_id": [">", 0]},
			or_filters=[{"item_code": item_code}, {"sku": item_code}],
			fields=["name"],
		)

		for item in woo_items:
			try:
				woo_item = frappe.get_doc("Woo Item", item.name)
				sync_stock_to_woo(woo_item)
				woo_item.db_set("last_synced", now_datetime())
				woo_item.db_set("sync_status", "Synced")
			except Exception:
				frappe.log_error(f"Real-time stock sync failed for Woo Item: {item.name}")
				frappe.get_doc("Woo Item", item.name).db_set("sync_status", "Error")

		frappe.db.commit()
	except Exception as e:
		frappe.log_error(title=f"Real-Time Stock Sync Failed - {item_code}", message=str(e))


def on_item_price_update(doc, method):
	"""Real-time event hook: Triggered whenever Item Price changes in ERPNext."""
	if not doc.item_code:
		return

	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not settings.enable_price_sync:
			return

		if settings.default_price_list and doc.price_list != settings.default_price_list:
			return

		frappe.enqueue(
			"woo_prime.api.sync.sync_price_for_item_code",
			item_code=doc.item_code,
			enqueue_after_commit=True,
			job_id=f"woo_price_{doc.item_code}",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(title="Real-Time Price Hook Exception", message=str(e))


def sync_price_for_item_code(item_code):
	"""Background worker: Push live updated price for a specific item (and variants) to WooCommerce and Woo Item."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not settings.enable_price_sync:
			return

		target_item_codes = [item_code]
		# If this item has variants, also include all its variant items
		variants = frappe.get_all(
			"Item",
			filters={"variant_of": item_code, "disabled": 0},
			pluck="name",
		)
		if variants:
			target_item_codes.extend(variants)

		woo_items = frappe.get_all(
			"Woo Item",
			filters={"sync_price": 1},
			or_filters=[{"item_code": ["in", target_item_codes]}, {"sku": ["in", target_item_codes]}],
			fields=["name"],
		)

		for item in woo_items:
			try:
				woo_item = frappe.get_doc("Woo Item", item.name)
				current_price = _get_item_price(woo_item.item_code, settings.default_price_list)
				if current_price > 0:
					woo_item.regular_price = flt(current_price)

				if woo_item.published and woo_item.woo_product_id:
					sync_price_to_woo(woo_item)
					woo_item.sync_status = "Synced"
					woo_item.last_synced = now_datetime()

				woo_item.flags.in_woo_sync = True
				woo_item.save(ignore_permissions=True)
			except Exception:
				frappe.log_error(f"Real-time price sync failed for Woo Item: {item.name}")
				try:
					frappe.get_doc("Woo Item", item.name).db_set("sync_status", "Error")
				except Exception:
					pass

		frappe.db.commit()
	except Exception as e:
		frappe.log_error(title=f"Real-Time Price Sync Failed - {item_code}", message=str(e))


def on_sales_order_status_change(doc, method):
	"""Real-time event hook: Triggered when Sales Order is submitted or cancelled in ERPNext.

	Pushes the updated order status (e.g. processing, completed, cancelled) to WooCommerce.
	"""
	if getattr(doc.flags, "from_woo", False):
		return

	woo_order_id = getattr(doc, "woo_order_id", None)
	if not woo_order_id:
		return

	# Do not overwrite WooCommerce status 'completed'
	if getattr(doc, "woo_order_status", "") == "completed":
		return

	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled:
			return

		from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
		api = get_woo_api()

		new_status = None
		if doc.docstatus == 1:  # Submitted
			new_status = getattr(settings, "submitted_order_status", "processing") or "processing"
		elif doc.docstatus == 2:  # Cancelled
			new_status = "cancelled"

		if new_status:
			api.update_order_status(woo_order_id, new_status)
			doc.db_set("woo_order_status", new_status)

			from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
			create_log(
				sync_type="Order",
				direction="Outgoing",
				status="Success",
				reference_doctype="Sales Order",
				reference_name=doc.name,
				woo_reference_id=str(woo_order_id),
				response_data=json.dumps({"action": "status_update", "status": new_status}),
			)
	except Exception as e:
		frappe.log_error(title=f"ERPNext → WooCommerce Order Status Sync Failed ({doc.name})", message=str(e))


def on_item_update(doc, method):
	"""Real-time event hook: Triggered whenever an Item Master record is saved/updated in ERPNext."""
	if not doc.name or getattr(doc.flags, "in_woo_sync", False) or getattr(frappe.flags, "in_test", False):
		return

	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled:
			return

		enable_item_sync = getattr(settings, "enable_item_sync", 1)
		if not enable_item_sync:
			return

		# 1. If this is a variant item, also sync its parent template item
		if getattr(doc, "variant_of", None):
			parent_woo_name = frappe.db.get_value("Woo Item", {"item_code": doc.variant_of})
			if parent_woo_name:
				parent_woo = frappe.get_doc("Woo Item", parent_woo_name)
				if parent_woo.published:
					frappe.enqueue(
						"woo_prime.woo_prime.doctype.woo_item.woo_item._publish_single_woo_item_worker",
						woo_item_name=parent_woo.name,
						user=frappe.session.user,
						queue="long",
						timeout=1800,
						enqueue_after_commit=True,
						job_id=f"woo_publish_{parent_woo.name}",
						deduplicate=True,
					)

		# 2. Check if a Woo Item exists for this item
		woo_item_name = frappe.db.get_value("Woo Item", {"item_code": doc.name})
		if not woo_item_name:
			woo_item_name = frappe.db.get_value("Woo Item", {"sku": doc.item_code or doc.name})

		if woo_item_name:
			woo_item = frappe.get_doc("Woo Item", woo_item_name)
			if woo_item.published:
				woo_item.flags.in_woo_sync = True
				if doc.item_name:
					woo_item.item_name = html.unescape(doc.item_name)
				if doc.description and not woo_item.woo_description:
					woo_item.woo_description = doc.description
				if getattr(doc, "image", None) and not woo_item.image:
					woo_item.image = doc.image
				woo_item.save(ignore_permissions=True)

				frappe.enqueue(
					"woo_prime.woo_prime.doctype.woo_item.woo_item._publish_single_woo_item_worker",
					woo_item_name=woo_item.name,
					user=frappe.session.user,
					queue="long",
					timeout=1800,
					enqueue_after_commit=True,
					job_id=f"woo_publish_{woo_item.name}",
					deduplicate=True,
				)
		elif getattr(settings, "auto_publish_new_items", 0):
			from woo_prime.woo_prime.doctype.woo_item.woo_item import publish_item_from_item_master
			publish_item_from_item_master(doc.name, background=True)

	except Exception as e:
		frappe.log_error(title=f"Item Auto Sync Hook Exception - {doc.name}", message=str(e))


def sync_all_items():
	"""Scheduled task: Daily auto-sync for published Woo Items."""
	settings = frappe.get_single("Woo Settings")
	if not settings.enabled or not getattr(settings, "enable_item_sync", 1):
		return

	from woo_prime.woo_prime.doctype.woo_item.woo_item import _is_within_sync_window
	if not _is_within_sync_window():
		return

	# Interval gate (default 1440 minutes = 1 day)
	interval_minutes = int(getattr(settings, "item_sync_interval_minutes", 1440) or 1440)
	last_sync = getattr(settings, "last_item_sync_time", None)
	if last_sync:
		from frappe.utils import get_datetime
		elapsed_minutes = (now_datetime() - get_datetime(last_sync)).total_seconds() / 60.0
		if elapsed_minutes < interval_minutes:
			return

	woo_items = frappe.get_all(
		"Woo Item",
		filters={"published": 1, "sync_status": "Synced"},
		fields=["name"],
		order_by="last_synced asc, creation asc",
		limit=100,
	)

	for item in woo_items:
		try:
			doc = frappe.get_doc("Woo Item", item.name)
			doc.flags.in_woo_sync = True
			doc._execute_publish_sync()
			frappe.db.commit()
		except Exception as e:
			frappe.log_error(f"Scheduled item sync failed for {item.name}: {e}")

	frappe.db.set_value("Woo Settings", "Woo Settings", "last_item_sync_time", now_datetime())
	frappe.db.commit()




def _publish_variant_as_simple_item(woo_item, item, api, settings):
	"""Publish a variant item as an independent simple product on WooCommerce.
	
	This is used when woo_product_type is set to 'Simple Item' for a variant.
	It will delete any existing variation relationship and create a standalone product.
	
	Args:
		woo_item: Woo Item document
		item: ERPNext Item document (variant item)
		api: WooCommerce API instance
		settings: Woo Settings document
		
	Returns:
		dict: WooCommerce product response
	"""
	import html
	
	# If this variant was previously published as a variation, delete it from WooCommerce
	if woo_item.woo_product_id:
		try:
			# Check if it's a variation by trying to get parent
			parent_item_name = item.variant_of
			if parent_item_name:
				parent_woo_item = frappe.db.get_value(
					"Woo Item",
					{"item_code": parent_item_name},
					["name", "woo_product_id"],
					as_dict=True
				)
				
				if parent_woo_item and parent_woo_item.woo_product_id:
					# Delete the variation from parent product
					try:
						api.delete(f"products/{parent_woo_item.woo_product_id}/variations/{woo_item.woo_product_id}", params={"force": True})
					except Exception as e:
						frappe.log_error(
							title=f"Failed to delete variation {woo_item.woo_product_id} from parent {parent_woo_item.woo_product_id}",
							message=str(e)
						)
		except Exception as e:
			frappe.log_error(
				title=f"Error checking/deleting previous variation for {woo_item.name}",
				message=str(e)
			)
		
		# Reset woo_product_id so it creates a new simple product
		woo_item.woo_product_id = None
	
	# Now publish as a simple product using the existing function
	return _publish_simple_item_to_woo(woo_item, item, api, settings)


def _publish_variant_as_new_template(woo_item, item, api, settings):
	"""Publish a variant item as a new template/variable product on WooCommerce.
	
	This is used when woo_product_type is set to 'Variant Item' for a variant.
	It will create a new variable product that can have its own variations.
	
	Args:
		woo_item: Woo Item document
		item: ERPNext Item document (variant item)
		api: WooCommerce API instance
		settings: Woo Settings document
		
	Returns:
		dict: WooCommerce product response
	"""
	import html
	
	# If this variant was previously published as a variation, delete it from WooCommerce
	if woo_item.woo_product_id:
		try:
			# Check if it's a variation by trying to get parent
			parent_item_name = item.variant_of
			if parent_item_name:
				parent_woo_item = frappe.db.get_value(
					"Woo Item",
					{"item_code": parent_item_name},
					["name", "woo_product_id"],
					as_dict=True
				)
				
				if parent_woo_item and parent_woo_item.woo_product_id:
					# Delete the variation from parent product
					try:
						api.delete(f"products/{parent_woo_item.woo_product_id}/variations/{woo_item.woo_product_id}", params={"force": True})
					except Exception as e:
						frappe.log_error(
							title=f"Failed to delete variation {woo_item.woo_product_id} from parent {parent_woo_item.woo_product_id}",
							message=str(e)
						)
		except Exception as e:
			frappe.log_error(
				title=f"Error checking/deleting previous variation for {woo_item.name}",
				message=str(e)
			)
		
		# Reset woo_product_id so it creates a new variable product
		woo_item.woo_product_id = None
	
	# Get attributes from the variant item to create as variable product attributes
	attributes_dict = {}
	for attr in item.attributes:
		if attr.attribute not in attributes_dict:
			attributes_dict[attr.attribute] = set()
		if attr.attribute_value:
			attributes_dict[attr.attribute].add(clean_text(attr.attribute_value))
	
	woo_attributes = []
	
	# Use attributes from woo_item.item_attributes if available, otherwise use item.attributes
	if getattr(woo_item, "item_attributes", None) and len(woo_item.item_attributes) > 0:
		for idx, row in enumerate(woo_item.item_attributes):
			attr_name = row.attribute_name or row.attribute
			if not attr_name:
				continue
			
			opts = [o.strip() for o in (row.options or "").replace("\n", ",").split(",") if o.strip()]
			
			woo_attr_id, target_name = _get_or_create_woo_attribute_id(attr_name, api)
			is_var = bool(getattr(row, "is_variation", 1))
			
			attr_payload = {
				"name": target_name or attr_name,
				"position": idx,  # Maintain order from child table
				"visible": bool(getattr(row, "is_visible", 1)),
				"variation": is_var,
				"options": opts if opts else [],
			}
			if woo_attr_id:
				attr_payload["id"] = int(woo_attr_id)
			woo_attributes.append(attr_payload)
	else:
		# Fallback to item attributes (alphabetical order)
		idx = 0
		for attr_name, options in sorted(attributes_dict.items()):
			woo_attr_id, target_name = _get_or_create_woo_attribute_id(attr_name, api)
			attr_payload = {
				"name": target_name or attr_name,
				"position": idx,
				"visible": True,
				"variation": True,
				"options": sorted(list(options))
			}
			if woo_attr_id:
				attr_payload["id"] = int(woo_attr_id)
			woo_attributes.append(attr_payload)
			idx += 1
	
	# Build product data for variable product
	product_name = woo_item.item_name or item.item_name or ""
	product_data = {
		"name": html.unescape(product_name),
		"type": "variable",
		"sku": woo_item.sku,
		"description": html.unescape(woo_item.woo_description or item.description or ""),
		"short_description": html.unescape(getattr(woo_item, "woo_short_description", None) or item.description or ""),
		"status": "publish",
		"attributes": woo_attributes,
		"manage_stock": bool(getattr(woo_item, "manage_stock", 0)),
	}
	
	# Add categories
	category_ids = _get_woo_category_ids(woo_item, api)
	if category_ids:
		product_data["categories"] = category_ids
	
	# Add images
	images = _get_item_images(item, woo_item, api)
	if images:
		product_data["images"] = images
	
	# Add tags
	woo_tag_ids = _get_woo_tag_ids(woo_item, api)
	if woo_tag_ids:
		product_data["tags"] = woo_tag_ids
	elif getattr(item, "_user_tags", None):
		tags_list = [{"name": t.strip()} for t in item._user_tags.split(",") if t.strip()]
		if tags_list:
			product_data["tags"] = tags_list
	
	# Create or update the product
	if woo_item.woo_product_id:
		result = api.update_product(woo_item.woo_product_id, product_data)
	else:
		# Check if product with same SKU exists
		existing_id = None
		if product_data.get("sku"):
			try:
				search_resp = api.get("products", params={"sku": product_data.get("sku"), "status": "any"})
				if search_resp.status_code == 200 and search_resp.json():
					existing_id = search_resp.json()[0].get("id")
			except Exception:
				frappe.log_error(title="woo_prime: SKU search error in _publish_variant_as_new_template", message=frappe.get_traceback())
		
		if existing_id:
			woo_item.woo_product_id = existing_id
			result = api.update_product(existing_id, product_data)
		else:
			result = api.create_product(product_data)
	
	# Update woo_item with response
	if result and result.get("id"):
		woo_item.woo_product_id = result.get("id")
		woo_item.published = 1
		permalink = result.get("permalink", "")
		woo_item.woo_product_url = html.unescape(permalink) if permalink else ""
		woo_item.sync_status = "Synced"
		woo_item.last_synced = now_datetime()
		woo_item.flags.in_woo_sync = True
		woo_item.save(ignore_permissions=True)
	
	return result
