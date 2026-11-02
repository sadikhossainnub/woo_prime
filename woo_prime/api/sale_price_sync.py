# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, today


def _get_precision(currency=None):
	"""Get currency precision from system settings or fallback to 2."""
	try:
		precision = cint(frappe.db.get_default("currency_precision"))
		if precision > 0:
			return precision
	except Exception:
		pass
	return 2


def _get_active_price_list(settings=None):
	"""Return the configured Selling Price List for WooCommerce.
	Prioritizes website_price_list, then default_price_list, then 'Standard Selling'.
	"""
	if not settings:
		settings = frappe.get_single("Woo Settings")
	return (
		getattr(settings, "website_price_list", None)
		or getattr(settings, "default_price_list", None)
		or "Standard Selling"
	)


def _is_general_rule(pricing_rule_doc):
	"""Verify if a Pricing Rule applies generally to all website visitors.

	Skips rules restricted to specific customers, non-default customer groups,
	territories, min quantity > 1, min amount > 0, or coupon code based rules.

	Returns:
		tuple: (is_general: bool, skip_reason: str or None)
	"""
	# Check selling flag (Pricing Rule must apply to Selling)
	rule_for = getattr(pricing_rule_doc, "applicable_for", None) or getattr(pricing_rule_doc, "rule_description", "")
	if hasattr(pricing_rule_doc, "selling") and not pricing_rule_doc.selling:
		return False, "Not a Selling Pricing Rule"

	# Specific customer restriction
	if getattr(pricing_rule_doc, "customer", None):
		return False, f"Restricted to specific customer: {pricing_rule_doc.customer}"

	# Specific customer group restriction
	customer_group = getattr(pricing_rule_doc, "customer_group", None)
	if customer_group and customer_group not in ("All Customer Groups", ""):
		# Check if it matches system default selling customer group
		default_cg = frappe.db.get_single_value("Selling Settings", "customer_group")
		if customer_group != default_cg:
			return False, f"Restricted to customer group: {customer_group}"

	# Specific territory restriction
	territory = getattr(pricing_rule_doc, "territory", None)
	if territory and territory not in ("All Territories", ""):
		default_terr = frappe.db.get_single_value("Selling Settings", "territory")
		if territory != default_terr:
			return False, f"Restricted to territory: {territory}"

	# Quantity threshold > 1
	min_qty = flt(getattr(pricing_rule_doc, "min_qty", 0))
	if min_qty > 1.0:
		return False, f"Min qty threshold is {min_qty} (> 1)"

	# Minimum amount threshold > 0
	min_amt = flt(getattr(pricing_rule_doc, "min_amt", 0))
	if min_amt > 0.0:
		return False, f"Min amount threshold is {min_amt} (> 0)"

	# Coupon code requirement
	if getattr(pricing_rule_doc, "coupon_code_based", 0):
		return False, "Coupon code based pricing rule"

	return True, None


def _get_items_for_pricing_rule(pricing_rule_doc):
	"""Resolve all Item codes governed by this Pricing Rule.
	Supports apply_on = 'Item Code', 'Item Group' (with child tree hierarchy), and 'Brand'.
	Also resolves template items into their active variants.

	Returns:
		list of str: Distinct ERPNext item codes
	"""
	apply_on = getattr(pricing_rule_doc, "apply_on", "Item Code")
	raw_item_codes = set()

	if apply_on == "Item Code":
		# Check child table 'items' (ERPNext v13+)
		if hasattr(pricing_rule_doc, "items") and pricing_rule_doc.items:
			for row in pricing_rule_doc.items:
				if getattr(row, "item_code", None):
					raw_item_codes.add(row.item_code)
		# Fallback to direct field 'item_code' (ERPNext v12 / legacy)
		if getattr(pricing_rule_doc, "item_code", None):
			raw_item_codes.add(pricing_rule_doc.item_code)

	elif apply_on == "Item Group":
		group_names = set()
		if hasattr(pricing_rule_doc, "item_groups") and pricing_rule_doc.item_groups:
			for row in pricing_rule_doc.item_groups:
				if getattr(row, "item_group", None):
					group_names.add(row.item_group)
		if getattr(pricing_rule_doc, "item_group", None):
			group_names.add(pricing_rule_doc.item_group)

		for grp in group_names:
			lft_rgt = frappe.db.get_value("Item Group", grp, ["lft", "rgt"], as_dict=True)
			if lft_rgt and lft_rgt.get("lft") and lft_rgt.get("rgt"):
				child_groups = frappe.get_all(
					"Item Group",
					filters={"lft": [">=", lft_rgt.lft], "rgt": ["<=", lft_rgt.rgt]},
					pluck="name",
				)
				if child_groups:
					matched_items = frappe.get_all(
						"Item",
						filters={"item_group": ["in", child_groups], "disabled": 0},
						pluck="name",
					)
					raw_item_codes.update(matched_items)
			else:
				matched_items = frappe.get_all(
					"Item",
					filters={"item_group": grp, "disabled": 0},
					pluck="name",
				)
				raw_item_codes.update(matched_items)

	elif apply_on == "Brand":
		brand_names = set()
		if hasattr(pricing_rule_doc, "brands") and pricing_rule_doc.brands:
			for row in pricing_rule_doc.brands:
				if getattr(row, "brand", None):
					brand_names.add(row.brand)
		if getattr(pricing_rule_doc, "brand", None):
			brand_names.add(pricing_rule_doc.brand)

		if brand_names:
			matched_items = frappe.get_all(
				"Item",
				filters={"brand": ["in", list(brand_names)], "disabled": 0},
				pluck="name",
			)
			raw_item_codes.update(matched_items)

	# Expand template items to include variants
	all_item_codes = set(raw_item_codes)
	for item_code in raw_item_codes:
		has_variants = frappe.db.get_value("Item", item_code, "has_variants")
		if has_variants:
			variant_codes = frappe.get_all(
				"Item",
				filters={"variant_of": item_code, "disabled": 0},
				pluck="name",
			)
			all_item_codes.update(variant_codes)

	return list(all_item_codes)


def _get_sale_price_for_item(item_code, regular_price, settings=None):
	"""Calculate effective sale price for an item using ERPNext's Pricing Rule engine.

	Uses erpnext.accounts.doctype.pricing_rule.pricing_rule.get_pricing_rule_for_item
	to ensure priorities, validities, UOMs, and rule conditions are evaluated by ERPNext.

	Args:
		item_code (str): ERPNext Item Code
		regular_price (float): Base regular price (price list rate)
		settings (Document, optional): Woo Settings singleton

	Returns:
		tuple: (sale_price: float or None, rule_doc_or_dict: dict or None, reason: str)
	"""
	if not item_code or regular_price <= 0:
		return None, None, "Invalid item code or regular price <= 0"

	if not settings:
		settings = frappe.get_single("Woo Settings")

	price_list = _get_active_price_list(settings)
	company = getattr(settings, "default_company", None)
	currency = None
	if price_list:
		currency = frappe.db.get_value("Price List", price_list, "currency")
	if not currency and company:
		currency = frappe.db.get_value("Company", company, "default_currency")
	if not currency:
		currency = getattr(settings, "currency", None) or "BDT"

	precision = _get_precision(currency)

	item_details = frappe.db.get_value("Item", item_code, ["item_group", "brand", "stock_uom"], as_dict=True) or {}
	item_group = item_details.get("item_group")
	brand = item_details.get("brand")
	uom = item_details.get("stock_uom")

	rule_args = frappe._dict({
		"doctype": "Sales Order",
		"parenttype": "Sales Order",
		"item_code": item_code,
		"item_group": item_group,
		"brand": brand,
		"qty": 1,
		"uom": uom,
		"stock_uom": uom,
		"rate": regular_price,
		"price_list": price_list,
		"currency": currency,
		"conversion_rate": 1.0,
		"plc_conversion_rate": 1.0,
		"transaction_type": "selling",
		"ignore_pricing_rule": 0,
		"customer": None,
		"customer_group": None,
		"territory": None,
		"company": company,
		"transaction_date": today(),
	})

	try:
		from erpnext.accounts.doctype.pricing_rule.pricing_rule import (
			get_pricing_rule_for_item,
		)
		rule_res = get_pricing_rule_for_item(rule_args)
	except ImportError:
		frappe.logger("woo_prime").error("ERPNext pricing_rule module not found")
		return None, None, "ERPNext pricing_rule module not found"
	except Exception as e:
		frappe.logger("woo_prime").error(f"Error calling get_pricing_rule_for_item for {item_code}: {e}")
		return None, None, str(e)

	if not rule_res:
		return None, None, "No active rule returned"

	rule_name = rule_res.get("pricing_rule")
	if not rule_name:
		return None, None, "No pricing_rule name in evaluation result"

	# Double check rule is general (not restricted to specific customer/group/min_qty)
	rule_doc = frappe.get_doc("Pricing Rule", rule_name)
	is_general, skip_reason = _is_general_rule(rule_doc)
	if not is_general:
		return None, None, f"Matched rule '{rule_name}' skipped: {skip_reason}"

	# Check rule validity dates
	today_date = getdate(today())
	if rule_doc.valid_from and getdate(rule_doc.valid_from) > today_date:
		return None, None, f"Rule '{rule_name}' not yet active (valid_from {rule_doc.valid_from})"
	if rule_doc.valid_upto and getdate(rule_doc.valid_upto) < today_date:
		return None, None, f"Rule '{rule_name}' expired (valid_upto {rule_doc.valid_upto})"

	discount_percentage = flt(rule_res.get("discount_percentage", 0))
	discount_amount = flt(rule_res.get("discount_amount", 0))
	rule_rate = rule_res.get("rate")
	pricing_rule_for = rule_res.get("pricing_rule_for")

	effective_sale_price = None

	# 1. Rate override rule
	if rule_rate is not None and (pricing_rule_for == "Rate" or getattr(rule_doc, "rate_or_discount", None) == "Rate"):
		effective_sale_price = flt(rule_rate)
	# 2. Discount Percentage rule
	elif discount_percentage > 0:
		discount_amt = (regular_price * discount_percentage) / 100.0
		effective_sale_price = regular_price - discount_amt
	# 3. Discount Amount rule
	elif discount_amount > 0:
		effective_sale_price = regular_price - discount_amount
	# 4. Fallback rate if rule returned a rate
	elif rule_rate is not None and flt(rule_rate) > 0:
		effective_sale_price = flt(rule_rate)

	if effective_sale_price is None:
		return None, rule_doc, "No discount or rate applied by rule"

	effective_sale_price = round(effective_sale_price, precision)

	# Validate: sale price must be strictly greater than 0 and less than regular price
	if effective_sale_price <= 0:
		return None, rule_doc, f"Calculated sale price {effective_sale_price} <= 0"
	if effective_sale_price >= regular_price:
		return None, rule_doc, f"Calculated sale price {effective_sale_price} >= regular price {regular_price}"

	return effective_sale_price, rule_doc, "Success"


def _format_woo_sale_dates(rule_doc):
	"""Format valid_from and valid_upto dates for WooCommerce product payload.
	WooCommerce expects YYYY-MM-DD or empty string.
	"""
	date_from = ""
	date_to = ""
	if rule_doc:
		if getattr(rule_doc, "valid_from", None):
			date_from = str(rule_doc.valid_from)
		if getattr(rule_doc, "valid_upto", None):
			date_to = str(rule_doc.valid_upto)
	return date_from, date_to


def sync_pricing_rule_to_woo(pricing_rule_name, dry_run=False):
	"""Sync all items covered by a specific Pricing Rule to WooCommerce as sale_price.

	Args:
		pricing_rule_name (str): Pricing Rule doc name
		dry_run (bool): If True, only log planned changes without calling WooCommerce API

	Returns:
		dict: Sync summary statistics
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	settings = frappe.get_single("Woo Settings")
	if not settings.enabled:
		frappe.logger("woo_prime").info(f"Woo Settings disabled; skipping Pricing Rule {pricing_rule_name}")
		return {"status": "skipped", "reason": "Woo Settings disabled"}

	if not getattr(settings, "enable_sale_price_sync", 1):
		frappe.logger("woo_prime").info(f"Sale price sync disabled in settings; skipping {pricing_rule_name}")
		return {"status": "skipped", "reason": "enable_sale_price_sync disabled"}

	if not frappe.db.exists("Pricing Rule", pricing_rule_name):
		frappe.logger("woo_prime").warning(f"Pricing Rule {pricing_rule_name} not found")
		return {"status": "skipped", "reason": "Pricing Rule not found"}

	pricing_rule_doc = frappe.get_doc("Pricing Rule", pricing_rule_name)

	# Check general rule eligibility
	is_general, skip_reason = _is_general_rule(pricing_rule_doc)
	if not is_general:
		create_log(
			sync_type="Price",
			direction="Outgoing",
			status="Skipped",
			reference_doctype="Pricing Rule",
			reference_name=pricing_rule_name,
			request_data={"pricing_rule": pricing_rule_name, "apply_on": getattr(pricing_rule_doc, "apply_on", "")},
			response_data={"skipped": True, "reason": skip_reason},
			error_message=f"Pricing Rule skipped: {skip_reason}",
		)
		return {"status": "skipped", "reason": skip_reason}

	# Check if disabled or expired
	is_disabled = cint(getattr(pricing_rule_doc, "disable", 0)) == 1
	is_expired = False
	today_date = getdate(today())
	if getattr(pricing_rule_doc, "valid_upto", None) and getdate(pricing_rule_doc.valid_upto) < today_date:
		is_expired = True

	affected_items = _get_items_for_pricing_rule(pricing_rule_doc)
	if not affected_items:
		return {"status": "skipped", "reason": "No items matched Pricing Rule"}

	# If disabled or expired, clear sale prices for items affected by this rule
	if is_disabled or is_expired:
		return clear_sale_price_for_items(affected_items, dry_run=dry_run, rule_name=pricing_rule_name)

	# Fetch mapped published Woo Items
	woo_items = frappe.get_all(
		"Woo Item",
		filters={
			"published": 1,
			"woo_product_id": [">", 0],
			"item_code": ["in", affected_items],
		},
		fields=["name", "item_code", "woo_product_id", "regular_price", "sale_price", "last_pushed_price", "last_pushed_sale_price", "woo_product_type"],
	)

	if not woo_items:
		return {"status": "skipped", "reason": "No published Woo Items found for affected items"}

	from woo_prime.api.sync import _get_item_price
	price_list = _get_active_price_list(settings)
	date_from, date_to = _format_woo_sale_dates(pricing_rule_doc)

	api = None if dry_run else get_woo_api()
	synced = 0
	skipped = 0
	errors = 0
	batch_updates = []
	batch_meta = []  # tuples of (woo_item_dict, regular_price, sale_price)

	for item in woo_items:
		try:
			# 1. Resolve regular price
			price = _get_item_price(item.item_code, price_list)
			reg_price = flt(price) if price > 0 else (flt(item.regular_price) if item.regular_price else 0.0)
			if reg_price <= 0:
				skipped += 1
				continue

			# 2. Evaluate effective sale price
			sale_price, matched_rule, reason = _get_sale_price_for_item(item.item_code, reg_price, settings)

			current_sale = flt(item.sale_price) if item.sale_price else None
			last_pushed_sale = flt(item.last_pushed_sale_price) if item.get("last_pushed_sale_price") else None
			last_pushed_reg = flt(item.last_pushed_price) if item.get("last_pushed_price") else None

			# 3. Check if price has changed (idempotent skip)
			regular_unchanged = (last_pushed_reg is not None and abs(last_pushed_reg - reg_price) < 0.001)
			sale_unchanged = (
				(sale_price is None and (current_sale is None or current_sale <= 0) and (last_pushed_sale is None or last_pushed_sale <= 0))
				or (sale_price is not None and last_pushed_sale is not None and abs(last_pushed_sale - sale_price) < 0.001)
			)

			if regular_unchanged and sale_unchanged:
				skipped += 1
				continue

			# 4. Check variation vs standalone product
			variant_of = frappe.db.get_value("Item", item.item_code, "variant_of")
			woo_product_type = item.get("woo_product_type") or ""
			parent_woo_id = None
			if variant_of and woo_product_type not in ("Simple Item", "Variant Item"):
				parent_woo_id = (
					frappe.db.get_value("Woo Item", {"item_code": variant_of}, "woo_product_id")
					or frappe.db.get_value("Woo Item", {"sku": variant_of}, "woo_product_id")
				)

			if dry_run:
				create_log(
					sync_type="Price",
					direction="Outgoing",
					status="Success",
					reference_doctype="Woo Item",
					reference_name=item.name,
					woo_reference_id=str(item.woo_product_id),
					request_data={
						"dry_run": True,
						"item_code": item.item_code,
						"regular_price": reg_price,
						"sale_price": sale_price if sale_price is not None else "",
						"date_on_sale_from": date_from,
						"date_on_sale_to": date_to,
					},
					response_data={"status": "Dry Run Simulation Success"},
				)
				synced += 1
				continue

			# Variations: update individually using single variation endpoint
			if parent_woo_id:
				try:
					api.update_variation_price(
						parent_woo_id,
						item.woo_product_id,
						reg_price,
						sale_price=sale_price,
						clear_sale=(sale_price is None),
					)
					_record_woo_item_price_success(item.name, reg_price, sale_price, str(item.woo_product_id))
					synced += 1
				except Exception as var_err:
					errors += 1
					frappe.log_error(f"Sale price sync variation error for {item.name}: {var_err}")
				continue

			# Standalone products: prepare for batch update
			p_data = {
				"id": int(item.woo_product_id),
				"regular_price": str(reg_price),
				"sale_price": str(sale_price) if sale_price is not None else "",
			}
			if date_from:
				p_data["date_on_sale_from"] = date_from
			if date_to:
				p_data["date_on_sale_to"] = date_to

			batch_updates.append(p_data)
			batch_meta.append((item, reg_price, sale_price))

		except Exception as item_err:
			errors += 1
			frappe.log_error(f"Error evaluating sale price for {item.name}: {item_err}")

	# Dispatch batches (100 products per request)
	chunk_size = 100
	for i in range(0, len(batch_updates), chunk_size):
		chunk_payload = batch_updates[i : i + chunk_size]
		chunk_meta = batch_meta[i : i + chunk_size]
		try:
			api.batch_products({"update": chunk_payload})
			for meta_item, reg_price, sale_price in chunk_meta:
				_record_woo_item_price_success(
					meta_item["name"], reg_price, sale_price, str(meta_item["woo_product_id"])
				)
				synced += 1
			frappe.db.commit()
		except Exception as batch_err:
			frappe.log_error(f"Batch sale price update error for Pricing Rule {pricing_rule_name}: {batch_err}")
			# Fallback to single item calls on batch failure
			for meta_item, reg_price, sale_price in chunk_meta:
				try:
					api.update_price(
						meta_item["woo_product_id"],
						reg_price,
						sale_price=sale_price,
						clear_sale=(sale_price is None),
					)
					_record_woo_item_price_success(
						meta_item["name"], reg_price, sale_price, str(meta_item["woo_product_id"])
					)
					synced += 1
				except Exception as single_err:
					errors += 1
					frappe.log_error(f"Single sale price fallback error for {meta_item['name']}: {single_err}")

	return {"status": "completed", "synced": synced, "skipped": skipped, "errors": errors}


def clear_sale_price_for_items(item_codes, dry_run=False, rule_name=""):
	"""Clear sale prices for items where Pricing Rule was disabled, deleted, or expired.
	Before clearing, evaluates if another active Pricing Rule applies to the item.
	If another rule applies, syncs that discounted price instead.

	Args:
		item_codes (list): List of item codes
		dry_run (bool): Dry run mode
		rule_name (str): Pricing Rule name that triggered the clear

	Returns:
		dict: Stats of operations
	"""
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	if not item_codes:
		return {"status": "skipped", "reason": "No items provided"}

	settings = frappe.get_single("Woo Settings")
	if not settings.enabled:
		return {"status": "skipped", "reason": "Woo Settings disabled"}

	woo_items = frappe.get_all(
		"Woo Item",
		filters={
			"published": 1,
			"woo_product_id": [">", 0],
			"item_code": ["in", item_codes],
		},
		fields=["name", "item_code", "woo_product_id", "regular_price", "sale_price", "last_pushed_price", "last_pushed_sale_price", "woo_product_type"],
	)

	from woo_prime.api.sync import _get_item_price
	price_list = _get_active_price_list(settings)
	api = None if dry_run else get_woo_api()

	cleared = 0
	updated = 0
	skipped = 0
	errors = 0
	batch_updates = []
	batch_meta = []

	for item in woo_items:
		try:
			price = _get_item_price(item.item_code, price_list)
			reg_price = flt(price) if price > 0 else (flt(item.regular_price) if item.regular_price else 0.0)
			if reg_price <= 0:
				skipped += 1
				continue

			# Check if any OTHER active pricing rule still covers this item
			alt_sale_price, alt_rule, _ = _get_sale_price_for_item(item.item_code, reg_price, settings)

			last_pushed_sale = flt(item.last_pushed_sale_price) if item.get("last_pushed_sale_price") else None
			current_sale = flt(item.sale_price) if item.sale_price else None

			if alt_sale_price is not None:
				# Another rule covers it! Update to the new rule's sale price
				if last_pushed_sale is not None and abs(last_pushed_sale - alt_sale_price) < 0.001:
					skipped += 1
					continue
				target_sale = alt_sale_price
				is_clearing = False
			else:
				# No active rule covers it; clear sale price
				if (current_sale is None or current_sale <= 0) and (last_pushed_sale is None or last_pushed_sale <= 0):
					skipped += 1
					continue
				target_sale = None
				is_clearing = True

			variant_of = frappe.db.get_value("Item", item.item_code, "variant_of")
			woo_product_type = item.get("woo_product_type") or ""
			parent_woo_id = None
			if variant_of and woo_product_type not in ("Simple Item", "Variant Item"):
				parent_woo_id = (
					frappe.db.get_value("Woo Item", {"item_code": variant_of}, "woo_product_id")
					or frappe.db.get_value("Woo Item", {"sku": variant_of}, "woo_product_id")
				)

			if dry_run:
				create_log(
					sync_type="Price",
					direction="Outgoing",
					status="Success",
					reference_doctype="Woo Item",
					reference_name=item.name,
					woo_reference_id=str(item.woo_product_id),
					request_data={"dry_run": True, "action": "clear" if is_clearing else "update", "sale_price": "" if is_clearing else target_sale},
					response_data={"status": "Dry Run Simulation Success"},
				)
				if is_clearing:
					cleared += 1
				else:
					updated += 1
				continue

			if parent_woo_id:
				try:
					api.update_variation_price(
						parent_woo_id,
						item.woo_product_id,
						reg_price,
						sale_price=target_sale,
						clear_sale=is_clearing,
					)
					_record_woo_item_price_success(item.name, reg_price, target_sale, str(item.woo_product_id))
					if is_clearing:
						cleared += 1
					else:
						updated += 1
				except Exception as var_err:
					errors += 1
					frappe.log_error(f"Variation sale price clear/update error: {var_err}")
				continue

			p_data = {
				"id": int(item.woo_product_id),
				"regular_price": str(reg_price),
				"sale_price": str(target_sale) if target_sale is not None else "",
				"date_on_sale_from": "",
				"date_on_sale_to": "",
			}
			batch_updates.append(p_data)
			batch_meta.append((item, reg_price, target_sale, is_clearing))

		except Exception as e:
			errors += 1
			frappe.log_error(f"Error in clear_sale_price_for_items for {item.name}: {e}")

	# Batch update
	chunk_size = 100
	for i in range(0, len(batch_updates), chunk_size):
		chunk_payload = batch_updates[i : i + chunk_size]
		chunk_meta = batch_meta[i : i + chunk_size]
		try:
			api.batch_products({"update": chunk_payload})
			for meta_item, reg_price, target_sale, is_clearing in chunk_meta:
				_record_woo_item_price_success(
					meta_item["name"], reg_price, target_sale, str(meta_item["woo_product_id"])
				)
				if is_clearing:
					cleared += 1
				else:
					updated += 1
			frappe.db.commit()
		except Exception as batch_err:
			frappe.log_error(f"Batch clear error for Pricing Rule {rule_name}: {batch_err}")
			for meta_item, reg_price, target_sale, is_clearing in chunk_meta:
				try:
					api.update_price(
						meta_item["woo_product_id"],
						reg_price,
						sale_price=target_sale,
						clear_sale=is_clearing,
					)
					_record_woo_item_price_success(
						meta_item["name"], reg_price, target_sale, str(meta_item["woo_product_id"])
					)
					if is_clearing:
						cleared += 1
					else:
						updated += 1
				except Exception as single_err:
					errors += 1
					frappe.log_error(f"Single clear fallback error for {meta_item['name']}: {single_err}")

	return {"status": "completed", "cleared": cleared, "updated": updated, "skipped": skipped, "errors": errors}


def _record_woo_item_price_success(woo_item_name, regular_price, sale_price, woo_product_id):
	"""Update Woo Item document and record success in Woo Sync Log."""
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	sale_val = flt(sale_price) if sale_price is not None and sale_price > 0 else 0.0

	frappe.db.set_value(
		"Woo Item",
		woo_item_name,
		{
			"regular_price": regular_price,
			"sale_price": sale_val,
			"last_pushed_price": regular_price,
			"last_pushed_sale_price": sale_val,
			"last_synced": now_datetime(),
			"sync_status": "Synced",
		},
		update_modified=False,
	)

	create_log(
		sync_type="Price",
		direction="Outgoing",
		status="Success",
		reference_doctype="Woo Item",
		reference_name=woo_item_name,
		woo_reference_id=woo_product_id,
		request_data={"regular_price": regular_price, "sale_price": sale_price if sale_price is not None else ""},
		response_data={"regular_price": regular_price, "sale_price": sale_price if sale_price is not None else "", "status": "Success"},
	)


def sync_all_sale_prices_daily():
	"""Scheduled daily job:
	1. Re-syncs all items covered by active, general Pricing Rules.
	2. Clears sale_price on WooCommerce for any Woo Items whose Pricing Rules have expired.
	"""
	settings = frappe.get_single("Woo Settings")
	if not settings.enabled or not getattr(settings, "enable_sale_price_sync", 1):
		return

	today_date = getdate(today())

	# 1. Active general rules
	active_rules = frappe.get_all(
		"Pricing Rule",
		filters={
			"disable": 0,
		},
		pluck="name",
	)

	synced_total = 0
	for rule_name in active_rules:
		try:
			rule_doc = frappe.get_doc("Pricing Rule", rule_name)
			if rule_doc.valid_upto and getdate(rule_doc.valid_upto) < today_date:
				# Expired rule
				clear_sale_price_for_rule(rule_name)
				continue
			res = sync_pricing_rule_to_woo(rule_name)
			synced_total += res.get("synced", 0)
		except Exception as e:
			frappe.log_error(f"Daily sale price sync error for Pricing Rule {rule_name}: {e}")

	# 2. Check for orphaned sale prices: Woo Items with sale_price > 0 where no active rule applies
	woo_items_with_sale = frappe.get_all(
		"Woo Item",
		filters={
			"published": 1,
			"woo_product_id": [">", 0],
			"sale_price": [">", 0],
		},
		pluck="item_code",
	)

	if woo_items_with_sale:
		clear_sale_price_for_items(woo_items_with_sale)

	frappe.logger("woo_prime").info(f"Daily sale price sync finished. Synced {synced_total} items.")


def clear_sale_price_for_rule(pricing_rule_name, dry_run=False):
	"""Clear sale price for items governed by a specific deleted or expired Pricing Rule."""
	if not frappe.db.exists("Pricing Rule", pricing_rule_name):
		return {"status": "skipped", "reason": "Pricing Rule does not exist"}

	rule_doc = frappe.get_doc("Pricing Rule", pricing_rule_name)
	affected_items = _get_items_for_pricing_rule(rule_doc)
	return clear_sale_price_for_items(affected_items, dry_run=dry_run, rule_name=pricing_rule_name)


def sync_sale_prices_manual(dry_run=False):
	"""Manual entry point for syncing all sale prices via button or API."""
	settings = frappe.get_single("Woo Settings")
	if not settings.enabled:
		return {"status": "error", "message": _("WooCommerce Integration is disabled in Woo Settings.")}

	today_date = getdate(today())
	active_rules = frappe.get_all(
		"Pricing Rule",
		filters={"disable": 0},
		pluck="name",
	)

	total_synced = 0
	total_cleared = 0
	total_skipped = 0
	total_errors = 0

	for rule_name in active_rules:
		try:
			rule_doc = frappe.get_doc("Pricing Rule", rule_name)
			if rule_doc.valid_upto and getdate(rule_doc.valid_upto) < today_date:
				clr_res = clear_sale_price_for_rule(rule_name, dry_run=dry_run)
				total_cleared += clr_res.get("cleared", 0)
				continue

			res = sync_pricing_rule_to_woo(rule_name, dry_run=dry_run)
			total_synced += res.get("synced", 0)
			total_skipped += res.get("skipped", 0)
			total_errors += res.get("errors", 0)
		except Exception as e:
			total_errors += 1
			frappe.log_error(f"Manual sale price sync error for rule {rule_name}: {e}")

	# Check orphaned sale prices
	woo_items_with_sale = frappe.get_all(
		"Woo Item",
		filters={
			"published": 1,
			"woo_product_id": [">", 0],
			"sale_price": [">", 0],
		},
		pluck="item_code",
	)
	if woo_items_with_sale:
		clr_res = clear_sale_price_for_items(woo_items_with_sale, dry_run=dry_run)
		total_cleared += clr_res.get("cleared", 0)

	return {
		"status": "success",
		"synced": total_synced,
		"cleared": total_cleared,
		"skipped": total_skipped,
		"errors": total_errors,
		"dry_run": dry_run,
	}


# -------------------------------------------------------------------------
# Event Hook Handlers
# -------------------------------------------------------------------------

def on_pricing_rule_update(doc, method):
	"""Hook handler on Pricing Rule save / update."""
	if getattr(doc.flags, "in_woo_sync", False):
		return

	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not getattr(settings, "enable_sale_price_sync", 1):
			return

		frappe.enqueue(
			"woo_prime.api.sale_price_sync.sync_pricing_rule_to_woo",
			pricing_rule_name=doc.name,
			queue="short",
			timeout=1800,
			enqueue_after_commit=True,
			job_id=f"woo_sale_price_{doc.name}",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(f"Pricing Rule update hook error for {doc.name}: {e}")


def on_pricing_rule_trash(doc, method):
	"""Hook handler on Pricing Rule delete or cancel."""
	try:
		settings = frappe.get_single("Woo Settings")
		if not settings.enabled or not getattr(settings, "enable_sale_price_sync", 1):
			return

		frappe.enqueue(
			"woo_prime.api.sale_price_sync.clear_sale_price_for_rule",
			pricing_rule_name=doc.name,
			queue="short",
			timeout=1800,
			enqueue_after_commit=True,
			job_id=f"woo_sale_price_clear_{doc.name}",
			deduplicate=True,
		)
	except Exception as e:
		frappe.log_error(f"Pricing Rule trash/cancel hook error for {doc.name}: {e}")

