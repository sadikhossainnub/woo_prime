# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import html
import json

import frappe
from frappe import _
from frappe.utils import now_datetime, flt, cstr, today, add_days


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

		woo_order_id = order_data.get("id")

		# Double-check idempotency (in case of race condition)
		if frappe.db.exists("Sales Order", {"woo_order_id": str(woo_order_id)}):
			return

		# 1. Find or create customer and addresses
		customer_name, billing_address, shipping_address = _get_or_create_customer(order_data, settings)

		# Transaction date & Delivery date (+7 days)
		transaction_date = _parse_date(order_data.get("date_created", today()))
		delivery_date = add_days(transaction_date, 7)

		# 2. Map line items
		items = _map_order_items(order_data, settings, delivery_date=delivery_date)

		if not items:
			_log_error(woo_order_id, order_data, "No valid items found in order")
			return

		# 3. Create Sales Order
		so = frappe.new_doc("Sales Order")
		so.customer = customer_name
		so.order_type = "Shopping Cart"
		so.sales_type = "Woo-commerce"
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

		if coupon_lines:
			coupons_applied = [c.get("code") for c in coupon_lines if c.get("code")]
			so.po_no = f"WC-{woo_order_id} (Coupons: {', '.join(coupons_applied)})"

			# Check if any coupon is for Loyalty Points redemption
			for coupon in coupon_lines:
				code = str(coupon.get("code", "")).lower()
				discount = flt(coupon.get("discount", 0))
				if "loyalty" in code or "point" in code:
					so.loyalty_amount = discount

		if discount_total > 0 and not so.loyalty_amount:
			# Apply overall additional discount if not handled via line item prices
			so.discount_amount = discount_total
			so.apply_discount_on = "Grand Total"

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

		# Resolve ERPNext Shipping Rule from WooCommerce Zone/Method Title or Mappings
		matched_rule = None
		if shipping_lines and hasattr(settings, "shipping_rule_mappings") and settings.shipping_rule_mappings:
			s_line = shipping_lines[0]
			method_title = (s_line.get("method_title") or "").strip().lower()
			method_id = (s_line.get("method_id") or "").strip().lower()

			for mapping in settings.shipping_rule_mappings:
				target_method = (mapping.woo_shipping_method or "").strip().lower()
				if target_method and (target_method == method_title or target_method == method_id or target_method in method_title):
					matched_rule = mapping.shipping_rule
					break

		if not matched_rule and shipping_title and frappe.db.exists("Shipping Rule", shipping_title):
			matched_rule = shipping_title

		if not matched_rule and getattr(settings, "default_shipping_rule", None):
			matched_rule = settings.default_shipping_rule

		if matched_rule:
			so.shipping_rule = matched_rule

		# Add taxes from template if configured
		if settings.tax_template:
			so.taxes_and_charges = settings.tax_template
			so.set_taxes()

		# Add shipping charges
		shipping_sync_mode = getattr(settings, "shipping_sync_type", "Line Item")

		if shipping_total > 0:
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

		# Customer notes
		customer_note = order_data.get("customer_note", "")
		if customer_note:
			so.add_comment("Comment", text=f"Customer Note (WooCommerce): {customer_note}")

		# Save and submit (respect auto_submit_order setting)
		so.flags.ignore_permissions = True
		so.flags.ignore_mandatory = True
		so.save()
		if getattr(settings, "auto_submit_order", 1):
			so.submit()

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
			response_data=json.dumps({"sales_order": so.name, "customer": customer_name}),
		)

		frappe.db.commit()

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(
			title=f"WooCommerce Order Sync Failed - {order_data.get('id', 'Unknown')}",
			message=frappe.get_traceback(),
		)
		_log_error(order_data.get("id"), order_data, str(e))


def update_order_from_woo(order_data, existing_so):
	"""Update an existing Sales Order from WooCommerce order update.

	Args:
		order_data: dict — WooCommerce order payload
		existing_so: str — existing Sales Order name
	"""
	try:
		so = frappe.get_doc("Sales Order", existing_so)

		# Only update status-related fields, don't modify items
		new_status = order_data.get("status", "")
		so.db_set("woo_order_status", new_status)

		from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

		create_log(
			sync_type="Order",
			direction="Incoming",
			status="Success",
			reference_doctype="Sales Order",
			reference_name=existing_so,
			woo_reference_id=str(order_data.get("id")),
			response_data=json.dumps({"action": "status_update", "new_status": new_status}),
		)
	except Exception as e:
		frappe.log_error(
			title=f"WooCommerce Order Update Failed - {existing_so}",
			message=frappe.get_traceback(),
		)


def _get_or_create_customer(order_data, settings):
	"""Find existing customer or create new one from WooCommerce billing data.

	Matching priority:
	1. Match by email
	2. Match by phone
	3. Create new customer

	Args:
		order_data: dict — WooCommerce order payload
		settings: Woo Settings document

	Returns:
		str: Customer name
	"""
	billing = order_data.get("billing", {})
	email = billing.get("email", "")
	phone = billing.get("phone", "")
	first_name = billing.get("first_name", "")
	last_name = billing.get("last_name", "")
	company_name = billing.get("company", "")

	full_name = f"{first_name} {last_name}".strip()
	if not full_name:
		full_name = email or f"WooCommerce Customer {order_data.get('id', '')}"

	customer_name = None

	# Try to find existing customer by email
	if email:
		existing = frappe.db.get_value(
			"Customer",
			{"email_id": email},
			"name"
		)
		if existing:
			customer_name = existing

	# Try to find by phone / mobile
	if not customer_name and phone:
		existing = frappe.db.get_value(
			"Customer",
			{"mobile_no": phone},
			"name"
		)
		if existing:
			customer_name = existing

	if not customer_name:
		# Auto-create customer if enabled
		if not settings.auto_create_customer:
			frappe.throw(
				_("Customer not found for email: {0}. Auto-create is disabled.").format(email)
			)

		# Create new customer
		customer = frappe.new_doc("Customer")
		customer.customer_name = company_name or full_name
		customer.customer_type = "Company" if company_name else "Individual"
		customer.customer_group = settings.default_customer_group
		customer.territory = settings.default_territory

		if email:
			customer.email_id = email
		if phone:
			customer.mobile_no = phone

		customer.flags.ignore_permissions = True
		customer.flags.ignore_mandatory = True
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

	# Log customer creation
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	create_log(
		sync_type="Customer",
		direction="Incoming",
		status="Success",
		reference_doctype="Customer",
		reference_name=customer_name,
		woo_reference_id=str(order_data.get("customer_id", "")),
	)

	return customer_name, billing_addr, shipping_addr


def _get_or_create_address(customer_name, billing, shipping):
	"""Create or find billing and shipping addresses for a customer.

	Args:
		customer_name: str — ERPNext customer name
		billing: dict — WooCommerce billing address data
		shipping: dict — WooCommerce shipping address data

	Returns:
		tuple: (billing_address_name, shipping_address_name)
	"""
	billing_addr_name = None
	shipping_addr_name = None

	# 1. Billing address
	if billing.get("address_1"):
		addr_line1 = billing.get("address_1", "")
		existing_billing = frappe.db.get_value(
			"Dynamic Link",
			{
				"link_doctype": "Customer",
				"link_name": customer_name,
				"parenttype": "Address",
			},
			"parent",
		)
		if existing_billing:
			billing_addr_name = existing_billing
		else:
			addr = frappe.new_doc("Address")
			addr.address_title = customer_name
			addr.address_type = "Billing"
			addr.address_line1 = addr_line1
			addr.address_line2 = billing.get("address_2", "")
			addr.city = billing.get("city", "")
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

	# 2. Shipping address (if provided and different or separate)
	has_shipping_data = shipping and (shipping.get("address_1") or shipping.get("city"))
	is_different = (
		has_shipping_data
		and (
			shipping.get("address_1") != billing.get("address_1")
			or shipping.get("city") != billing.get("city")
		)
	)

	if is_different:
		addr = frappe.new_doc("Address")
		addr.address_title = f"{customer_name} - Shipping"
		addr.address_type = "Shipping"
		addr.address_line1 = shipping.get("address_1", "")
		addr.address_line2 = shipping.get("address_2", "")
		addr.city = shipping.get("city", "")
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
		# If shipping address is same as billing, set shipping_addr_name to billing_addr_name
		shipping_addr_name = billing_addr_name

	return billing_addr_name, shipping_addr_name


def _create_contact(customer_name, first_name, last_name, email, phone):
	"""Create a contact linked to the customer.

	Args:
		customer_name: str — ERPNext customer name
		first_name: str
		last_name: str
		email: str
		phone: str
	"""
	try:
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

	Looks up each item by SKU in the Woo Item doctype.
	If SKU not found in Woo Item, tries to find ERPNext Item directly by item_code = SKU.

	Args:
		order_data: dict — WooCommerce order payload
		settings: Woo Settings document
		delivery_date: str — Delivery date for items

	Returns:
		list: List of dicts for Sales Order items table
	"""
	items = []
	line_items = order_data.get("line_items", [])
	item_delivery_date = delivery_date or _parse_date(order_data.get("date_created", today()))

	for line in line_items:
		sku = (line.get("sku") or "").strip()
		qty = flt(line.get("quantity", 1))
		rate = flt(line.get("price", 0))
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

		# 4. Fallback: try to find by item_name (both exact and html.unescaped)
		if not item_code and item_name_wc:
			clean_name = html.unescape(item_name_wc)
			item_code = (
				frappe.db.get_value("Item", {"item_name": clean_name}, "name")
				or frappe.db.get_value("Item", {"item_name": item_name_wc}, "name")
			)

		if not item_code:
			frappe.log_error(
				title=f"WooCommerce Item Not Found - SKU: {sku}",
				message=f"Could not find ERPNext item for WooCommerce line item.\n"
				f"SKU: {sku}\nName: {item_name_wc}\nProduct ID: {prod_id_wc}\nOrder ID: {order_data.get('id')}",
			)
			continue

		items.append({
			"item_code": item_code,
			"qty": qty,
			"rate": rate,
			"delivery_date": item_delivery_date,
			"warehouse": settings.default_warehouse,
		})

	return items


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
		request_data=json.dumps(order_data)[:10000] if order_data else "",
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

	if item.variant_of:
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

	product_data = {
		"name": html.unescape(item.item_name or ""),
		"sku": woo_item.sku,
		"regular_price": str(flt(regular_price)),
		"description": html.unescape(woo_item.woo_description or item.description or ""),
		"short_description": html.unescape(getattr(woo_item, "woo_short_description", None) or item.description or ""),
		"manage_stock": True,
		"stock_quantity": int(stock_qty),
		"status": "publish",
	}
	if sale_price is not None:
		product_data["sale_price"] = str(flt(sale_price))

	category_ids = _get_woo_category_ids(woo_item, api)
	if category_ids:
		product_data["categories"] = category_ids

	simple_attrs = _get_simple_item_attributes(item)
	if simple_attrs:
		product_data["attributes"] = simple_attrs

	images = _get_item_images(item, woo_item, api)
	if images:
		product_data["images"] = images

	if woo_item.woo_product_id:
		result = api.update_product(woo_item.woo_product_id, product_data)
	else:
		result = api.create_product(product_data)

	return result


def _publish_template_item_to_woo(woo_item, item, api, settings):
	"""Publish an ERPNext Template Item and all its variants as a WooCommerce Variable Product."""
	import html
	variant_item_names = frappe.get_all(
		"Item",
		filters={"variant_of": item.name, "disabled": 0},
		pluck="name"
	)

	attributes_dict = {}
	for v_name in variant_item_names:
		v_item = frappe.get_doc("Item", v_name)
		for attr in v_item.attributes:
			if attr.attribute not in attributes_dict:
				attributes_dict[attr.attribute] = set()
			attributes_dict[attr.attribute].add(attr.attribute_value)

	woo_attributes = []
	for attr_name, options in attributes_dict.items():
		woo_attributes.append({
			"name": attr_name,
			"visible": True,
			"variation": True,
			"options": sorted(list(options))
		})

	product_data = {
		"name": html.unescape(item.item_name or ""),
		"type": "variable",
		"sku": woo_item.sku,
		"description": html.unescape(woo_item.woo_description or item.description or ""),
		"short_description": html.unescape(getattr(woo_item, "woo_short_description", None) or item.description or ""),
		"status": "publish",
		"attributes": woo_attributes,
	}

	category_ids = _get_woo_category_ids(woo_item, api)
	if category_ids:
		product_data["categories"] = category_ids

	images = _get_item_images(item, woo_item, api)
	if images:
		product_data["images"] = images

	if woo_item.woo_product_id:
		result = api.update_product(woo_item.woo_product_id, product_data)
	else:
		result = api.create_product(product_data)

	parent_woo_id = result.get("id")
	woo_item.woo_product_id = parent_woo_id
	woo_item.published = 1
	woo_item.sync_status = "Synced"
	woo_item.last_synced = now_datetime()
	woo_item.save(ignore_permissions=True)

	for v_name in variant_item_names:
		v_item = frappe.get_doc("Item", v_name)
		child_woo_item = get_or_create_woo_item(v_name, v_item.item_code)
		_publish_variation_data(parent_woo_id, child_woo_item, v_item, api, settings)

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
		if changed:
			woo_item.save(ignore_permissions=True)
		return woo_item

	# Create new Woo Item doc safely
	woo_item = frappe.new_doc("Woo Item")
	woo_item.item_code = item_code
	woo_item.sku = sku or item_code
	try:
		woo_item.insert(ignore_permissions=True)
	except Exception as e:
		target_name = sku or item_code
		if frappe.db.exists("Woo Item", target_name):
			woo_item = frappe.get_doc("Woo Item", target_name)
			if not woo_item.item_code and item_code:
				woo_item.item_code = item_code
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


def _publish_variation_data(parent_woo_id, child_woo_item, v_item, api, settings):
	"""Publish a single variation to WooCommerce under parent variable product."""
	import html
	price = _get_item_price(v_item.name, settings.default_price_list)
	regular_price = flt(child_woo_item.regular_price) if getattr(child_woo_item, "regular_price", None) else flt(price)
	sale_price = flt(child_woo_item.sale_price) if getattr(child_woo_item, "sale_price", None) and flt(child_woo_item.sale_price) > 0 else None
	stock_qty = _get_stock_qty(v_item.name, settings.default_warehouse)

	var_attributes = []
	for attr in v_item.attributes:
		var_attributes.append({
			"name": attr.attribute,
			"option": attr.attribute_value
		})

	var_data = {
		"sku": child_woo_item.sku or v_item.item_code,
		"regular_price": str(flt(regular_price)),
		"manage_stock": True,
		"stock_quantity": int(stock_qty),
		"attributes": var_attributes,
		"description": html.unescape(child_woo_item.woo_description or v_item.description or ""),
	}
	if sale_price is not None:
		var_data["sale_price"] = str(flt(sale_price))

	images = _get_item_images(v_item, child_woo_item, api)
	if images:
		var_data["image"] = images[0]

	if child_woo_item.woo_product_id:
		res = api.update_product_variation(parent_woo_id, child_woo_item.woo_product_id, var_data)
	else:
		res = api.create_product_variation(parent_woo_id, var_data)

	child_woo_item.woo_product_id = res.get("id")
	child_woo_item.published = 1
	child_woo_item.sync_status = "Synced"
	child_woo_item.last_synced = now_datetime()
	child_woo_item.save(ignore_permissions=True)
	return res


def _get_item_images(item, woo_item=None, api=None):
	"""Get image payload for WooCommerce (attempts direct media upload to WordPress API first)."""
	images = []
	seen_paths = set()

	def process_image(path, caption=None):
		if not path or path in seen_paths:
			return
		seen_paths.add(path)

		is_private = path.startswith("/private/files/") or path.startswith("private/files/")

		# 1. Direct binary upload to WordPress Media REST API (/wp-json/wp/v2/media)
		if api and not (path.startswith("http://") or path.startswith("https://")):
			try:
				uploaded = api.upload_media(path)
				if uploaded and uploaded.get("id"):
					img_dict = {"id": uploaded.get("id")}
					if caption:
						img_dict["alt"] = caption
					images.append(img_dict)
					return
			except Exception as e:
				frappe.log_error(
					title="WooCommerce Image Upload Failed",
					message=f"Direct upload failed for {path}: {e}"
				)

			# If direct upload failed for a private file, do NOT fall back to URL —
			# WooCommerce cannot access /private/files/ (requires Frappe auth, returns 403).
			if is_private:
				frappe.msgprint(
					f"Could not upload private image <b>{path}</b> to WooCommerce. "
					"Please move the file to public or re-attach it.",
					indicator="orange",
					alert=True,
				)
				return

		# 2. Fallback to image URL if direct upload fails or for external URLs
		if path.startswith("http://") or path.startswith("https://"):
			url = path
		else:
			url = frappe.utils.get_url(path)

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


def _get_or_create_woo_category_id(cat_name, api):
	"""Search or create WooCommerce category by name and return its WooCommerce ID (preserving parent-child link)."""
	try:
		if frappe.db.exists("Woo Category", cat_name):
			from woo_prime.woo_prime.doctype.woo_category.woo_category import push_category_to_woo
			res = push_category_to_woo(cat_name)
			if res and isinstance(res, dict) and res.get("id"):
				return res.get("id")

		response = api.get("products/categories", params={"search": cat_name})
		if response.status_code == 200:
			categories = response.json()
			for cat in categories:
				if cat.get("name", "").strip().lower() == cat_name.strip().lower():
					_save_woo_category_loc(cat_name, cat.get("id"), cat.get("slug"))
					return cat.get("id")

		create_resp = api.post("products/categories", data={"name": cat_name})
		if create_resp.status_code in (200, 201):
			new_cat = create_resp.json()
			cat_id = new_cat.get("id")
			_save_woo_category_loc(cat_name, cat_id, new_cat.get("slug"))
			return cat_id
	except Exception:
		pass
	return None


def _get_simple_item_attributes(item):
	"""Extract Item Attributes from ERPNext Item child table for simple products."""
	if not hasattr(item, "attributes") or not item.attributes:
		return []

	woo_attrs = []
	for attr in item.attributes:
		if attr.attribute and attr.attribute_value:
			woo_attrs.append({
				"name": attr.attribute,
				"visible": True,
				"variation": False,
				"options": [attr.attribute_value]
			})
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
		pass


def sync_stock_to_woo(woo_item):
	"""Push current ERPNext stock to WooCommerce.

	Args:
		woo_item: Woo Item document
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	api = get_woo_api()
	settings = frappe.get_single("Woo Settings")

	stock_qty = _get_stock_qty(woo_item.item_code, settings.default_warehouse)
	api.update_stock(woo_item.woo_product_id, stock_qty)

	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	create_log(
		sync_type="Stock",
		direction="Outgoing",
		status="Success",
		reference_doctype="Woo Item",
		reference_name=woo_item.name,
		woo_reference_id=str(woo_item.woo_product_id),
		response_data=json.dumps({"stock_quantity": stock_qty}),
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
	regular_price = flt(woo_item.regular_price) if getattr(woo_item, "regular_price", None) else flt(price)
	sale_price = flt(woo_item.sale_price) if getattr(woo_item, "sale_price", None) and flt(woo_item.sale_price) > 0 else None

	api.update_price(woo_item.woo_product_id, regular_price, sale_price=sale_price)

	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	create_log(
		sync_type="Price",
		direction="Outgoing",
		status="Success",
		reference_doctype="Woo Item",
		reference_name=woo_item.name,
		woo_reference_id=str(woo_item.woo_product_id),
		response_data=json.dumps({"regular_price": regular_price, "sale_price": sale_price}),
	)


def _get_item_price(item_code, price_list):
	"""Get item price from ERPNext price list.

	Args:
		item_code: str
		price_list: str

	Returns:
		float: price rate
	"""
	price = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": price_list, "selling": 1},
		"price_list_rate",
	)
	return flt(price)


def _get_stock_qty(item_code, warehouse):
	"""Get current stock quantity from ERPNext.

	Args:
		item_code: str
		warehouse: str

	Returns:
		float: actual stock quantity
	"""
	from erpnext.stock.utils import get_latest_stock_qty

	return flt(get_latest_stock_qty(item_code, warehouse))


# ═══════════════════════════════════════════════════
# Scheduled Tasks
# ═══════════════════════════════════════════════════


def sync_all_stock():
	"""Scheduled task: Push all Woo Item stock to WooCommerce.

	Runs on configured schedule (e.g., hourly) to keep stock in sync.
	"""
	settings = frappe.get_single("Woo Settings")
	if not settings.enabled or not settings.enable_stock_sync:
		return

	woo_items = frappe.get_all(
		"Woo Item",
		filters={"published": 1, "sync_stock": 1, "woo_product_id": [">", 0]},
		fields=["name"],
	)

	for item in woo_items:
		try:
			woo_item = frappe.get_doc("Woo Item", item.name)
			sync_stock_to_woo(woo_item)
			woo_item.db_set("last_synced", now_datetime())
			woo_item.db_set("sync_status", "Synced")
		except Exception:
			frappe.log_error(f"Stock sync failed for Woo Item: {item.name}")
			frappe.get_doc("Woo Item", item.name).db_set("sync_status", "Error")

	frappe.db.commit()


def sync_all_prices():
	"""Scheduled task: Push all Woo Item prices to WooCommerce.

	Runs on configured schedule to keep prices in sync.
	"""
	settings = frappe.get_single("Woo Settings")
	if not settings.enabled or not settings.enable_price_sync:
		return

	woo_items = frappe.get_all(
		"Woo Item",
		filters={"published": 1, "sync_price": 1, "woo_product_id": [">", 0]},
		fields=["name"],
	)

	for item in woo_items:
		try:
			woo_item = frappe.get_doc("Woo Item", item.name)
			sync_price_to_woo(woo_item)
			woo_item.db_set("last_synced", now_datetime())
			woo_item.db_set("sync_status", "Synced")
		except Exception:
			frappe.log_error(f"Price sync failed for Woo Item: {item.name}")
			frappe.get_doc("Woo Item", item.name).db_set("sync_status", "Error")

	frappe.db.commit()


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

		orders = api.get_orders(params={
			"after": after_date,
			"per_page": 100,
			"status": "processing,completed",
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
			at_front=True,
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
			at_front=True,
		)
	except Exception as e:
		frappe.log_error(title="Real-Time Price Hook Exception", message=str(e))


def sync_price_for_item_code(item_code):
	"""Background worker: Push live updated price for a specific item to WooCommerce."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not settings.enable_price_sync:
			return

		woo_items = frappe.get_all(
			"Woo Item",
			filters={"published": 1, "sync_price": 1, "woo_product_id": [">", 0]},
			or_filters=[{"item_code": item_code}, {"sku": item_code}],
			fields=["name"],
		)

		for item in woo_items:
			try:
				woo_item = frappe.get_doc("Woo Item", item.name)
				sync_price_to_woo(woo_item)
				woo_item.db_set("last_synced", now_datetime())
				woo_item.db_set("sync_status", "Synced")
			except Exception:
				frappe.log_error(f"Real-time price sync failed for Woo Item: {item.name}")
				frappe.get_doc("Woo Item", item.name).db_set("sync_status", "Error")

		frappe.db.commit()
	except Exception as e:
		frappe.log_error(title=f"Real-Time Price Sync Failed - {item_code}", message=str(e))
