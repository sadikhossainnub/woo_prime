# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

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

		# Add shipping as a line item (if applicable)
		shipping_total = flt(order_data.get("shipping_total", 0))
		if shipping_total > 0 and settings.shipping_item:
			so.append("items", {
				"item_code": settings.shipping_item,
				"qty": 1,
				"rate": shipping_total,
				"delivery_date": so.delivery_date,
				"warehouse": settings.default_warehouse,
			})

		# Add taxes if configured
		if settings.tax_template:
			so.taxes_and_charges = settings.tax_template
			# Let ERPNext calculate taxes from template
			so.set_taxes()

		# Customer notes
		customer_note = order_data.get("customer_note", "")
		if customer_note:
			so.add_comment("Comment", text=f"Customer Note (WooCommerce): {customer_note}")

		# Save and submit
		so.flags.ignore_permissions = True
		so.flags.ignore_mandatory = True
		so.save()
		so.submit()

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
		sku = line.get("sku", "")
		qty = flt(line.get("quantity", 1))
		rate = flt(line.get("price", 0))
		item_name_wc = line.get("name", "")

		item_code = None

		# First, try to find in Woo Item by SKU
		if sku:
			woo_item = frappe.db.get_value("Woo Item", sku, "item_code")
			if woo_item:
				item_code = woo_item

		# Fallback: try to find ERPNext Item directly by SKU as item_code
		if not item_code and sku:
			if frappe.db.exists("Item", sku):
				item_code = sku

		# Fallback: try to find by item_name
		if not item_code and item_name_wc:
			item_code = frappe.db.get_value("Item", {"item_name": item_name_wc}, "name")

		if not item_code:
			frappe.log_error(
				title=f"WooCommerce Item Not Found - SKU: {sku}",
				message=f"Could not find ERPNext item for WooCommerce line item.\n"
				f"SKU: {sku}\nName: {item_name_wc}\nOrder ID: {order_data.get('id')}",
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
	"""Publish an ERPNext item to WooCommerce.

	Args:
		woo_item: Woo Item document

	Returns:
		dict: WooCommerce product response
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

	api = get_woo_api()
	settings = frappe.get_single("Woo Settings")

	# Get ERPNext item details
	item = frappe.get_doc("Item", woo_item.item_code)

	# Get price from price list
	price = _get_item_price(woo_item.item_code, settings.default_price_list)

	# Get stock qty
	stock_qty = _get_stock_qty(woo_item.item_code, settings.default_warehouse)

	# Build product data
	product_data = {
		"name": item.item_name,
		"sku": woo_item.sku,
		"regular_price": str(flt(price)),
		"description": woo_item.woo_description or item.description or "",
		"short_description": item.description or "",
		"manage_stock": True,
		"stock_quantity": int(stock_qty),
		"status": "publish",
	}

	# Add category if specified
	if woo_item.woo_category:
		# Try to find WooCommerce category by name
		categories_response = api.get("products/categories", params={"search": woo_item.woo_category})
		if categories_response.status_code == 200:
			categories = categories_response.json()
			if categories:
				product_data["categories"] = [{"id": categories[0]["id"]}]

	# Create or update
	if woo_item.woo_product_id:
		result = api.update_product(woo_item.woo_product_id, product_data)
	else:
		result = api.create_product(product_data)

	return result


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
	api.update_price(woo_item.woo_product_id, price)

	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	create_log(
		sync_type="Price",
		direction="Outgoing",
		status="Success",
		reference_doctype="Woo Item",
		reference_name=woo_item.name,
		woo_reference_id=str(woo_item.woo_product_id),
		response_data=json.dumps({"price": price}),
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

		# Fetch orders from last 2 days
		import datetime

		after_date = (datetime.datetime.now() - datetime.timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")

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
