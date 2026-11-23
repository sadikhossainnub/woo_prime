# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import re
import html
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime
from woo_prime.utils import clean_text


from woo_prime.api.sync import _should_skip_variant


class WooItem(Document):
	def validate(self):
		from frappe.utils import flt
		if self.item_code:
			# Cache has_variants and variant_of for UI visibility and stock handling
			item_data = frappe.db.get_value("Item", self.item_code, ["has_variants", "variant_of"], as_dict=True)
			if item_data:
				self.has_variants = cint(item_data.has_variants)
				self.variant_of = item_data.variant_of or ""
				
				# Auto-set woo_product_type for new items based on ERPNext item type
				if self.is_new() or self.has_value_changed("item_code"):
					if self.has_variants and not self.woo_product_type:
						# Template item → default to "Variant Item" (Variable Product in WooCommerce)
						self.woo_product_type = "Variant Item"
					elif not self.has_variants and not self.variant_of and not self.woo_product_type:
						# Simple item → default to "Simple Item"
						self.woo_product_type = "Simple Item"

				# Disallow setting woo_product_type = "Variant Item" on a variant
				if self.variant_of and self.woo_product_type == "Variant Item":
					frappe.throw(_("'Variant Item' is not allowed for variants."))

				# Stock management rules (honor user choice; only set defaults on new docs):
				if self.has_variants:
					# Template Variable Product: stock NEVER on parent, always variation-level
					self.manage_stock = 0
					if self.is_new() and getattr(self, "manage_variation_stock", None) is None:
						self.manage_variation_stock = 1
				elif self.variant_of:
					self.manage_variation_stock = 0
					if self.is_new() and getattr(self, "manage_stock", None) is None:
						self.manage_stock = 1
				else:
					self.manage_variation_stock = 0
					if self.is_new() and getattr(self, "manage_stock", None) is None:
						self.manage_stock = 1

			# Fetch item_name if not set
			if not self.item_name:
				raw_name = frappe.db.get_value("Item", self.item_code, "item_name")
				self.item_name = html.unescape(raw_name) if raw_name else ""
			elif self.item_name:
				self.item_name = html.unescape(self.item_name)

			# Auto-sync image from Item if not set on Woo Item
			if not getattr(self, "image", None):
				item_img = frappe.db.get_value("Item", self.item_code, "image")
				if item_img:
					self.image = item_img

			# Auto-fetch regular_price and evaluate Pricing Rule sale_price
			settings = frappe.get_single("Woo Settings")
			from woo_prime.api.sync import _get_item_price, _resolve_price_list
			price_list = _resolve_price_list(settings)
			price = _get_item_price(self.item_code, price_list)
			if price and flt(price) > 0 and (not self.regular_price or flt(self.regular_price) <= 0):
				self.regular_price = flt(price)

			# Only update sale_price if this item has an active Pricing Rule
			if getattr(settings, "enable_sale_price_sync", 1):
				base_p = flt(self.regular_price) if self.regular_price else flt(price)
				if base_p > 0:
					try:
						from woo_prime.api.sale_price_sync import _get_sale_price_for_item
						calc_sale, _, _ = _get_sale_price_for_item(self.item_code, base_p, settings)
						if calc_sale is not None and flt(calc_sale) > 0:
							self.sale_price = flt(calc_sale)
					except Exception as e:
						frappe.logger("woo_prime").debug(f"Pricing rule calc notice: {e}")

			# Auto-fetch description from Item Master if woo_description is empty
			if not self.woo_description:
				raw_desc = frappe.db.get_value("Item", self.item_code, "description")
				if raw_desc:
					self.woo_description = clean_table_html_for_editor(html.unescape(str(raw_desc)))

		if self.woo_description:
			self.woo_description = clean_table_html_for_editor(self.woo_description)

		if self.woo_product_url:
			self.woo_product_url = html.unescape(str(self.woo_product_url)).strip()

		# Auto-populate product attributes from ERPNext if table is empty
		self._auto_populate_attributes()

		# Ensure item_attributes have options populated or fallback from master
		self._validate_and_fill_attribute_options()

	def _validate_and_fill_attribute_options(self):
		"""Ensure no attribute row is left with empty options if terms can be discovered."""
		for row in (self.item_attributes or []):
			if not row.options and row.attribute:
				terms_data = get_attribute_terms_for_woo_item(row.attribute, item_code=self.item_code)
				terms = terms_data.get("terms", [])
				if terms:
					row.options = ", ".join(terms)

	def _auto_populate_attributes(self):
		"""Auto-populate item_attributes child table from ERPNext Item / Variants if empty.
		
		Only runs during API sync (when in_woo_sync flag is set).
		For manual creation, user must explicitly click 'Fetch Attributes from ERPNext' button.
		"""
		# Only auto-populate during API sync, not manual form entry
		if not getattr(self.flags, "in_woo_sync", False):
			return
		
		if getattr(self, "item_attributes", None) and len(self.item_attributes) > 0:
			return
		if not self.item_code or not frappe.db.exists("Item", self.item_code):
			return
		self._load_attributes_from_erpnext(overwrite=False)

	def _load_attributes_from_erpnext(self, overwrite=False):
		"""Extract and load attributes from ERPNext Item or its variants, linking them to Woo Attribute.
		
		Auto-creates Woo Attribute documents only during API sync (when in_woo_sync flag is set).
		For manual operations, Woo Attributes must be created beforehand.
		"""
		if not self.item_code or not frappe.db.exists("Item", self.item_code):
			return

		# Check if we should auto-create Woo Attributes
		# Only auto-create during API sync operations
		auto_create_attributes = getattr(self.flags, "in_woo_sync", False)

		item = frappe.get_doc("Item", self.item_code)
		if overwrite:
			self.set("item_attributes", [])

		# Bug #3 fix: index by BOTH woo_attr name AND erpnext attribute_name to prevent duplicate rows
		existing_attrs = {}
		for r in (self.item_attributes or []):
			if r.attribute:
				existing_attrs[r.attribute] = r
			if r.attribute_name:
				existing_attrs[r.attribute_name] = r

		if getattr(item, "has_variants", 0):
			variant_names = frappe.get_all(
				"Item",
				filters={"variant_of": item.name, "disabled": 0},
				pluck="name"
			)
			attr_values = {}

			# Bug #1 fix: single batch query instead of N+1 frappe.get_doc() calls
			if variant_names:
				all_variant_attrs = frappe.get_all(
					"Item Variant Attribute",
					filters={"parent": ["in", variant_names]},
					fields=["parent", "attribute", "attribute_value"],
				)
				for a in all_variant_attrs:
					if a.attribute:
						val = clean_text(a.attribute_value) if a.attribute_value else ""
						if val:
							attr_values.setdefault(a.attribute, set()).add(val)
						else:
							# Bug #2 fix: register attribute even when value is empty
							attr_values.setdefault(a.attribute, set())

			# Include template-level defined attributes
			for a in (item.attributes or []):
				if a.attribute and a.attribute not in attr_values:
					attr_values[a.attribute] = set()
					if a.attribute_value:
						attr_values[a.attribute].add(clean_text(a.attribute_value))

			for attr_name, vals in attr_values.items():
				woo_attr = _get_or_create_woo_attribute_doc(attr_name, auto_create=auto_create_attributes)
				if not woo_attr:
					# Skip this attribute if Woo Attribute doesn't exist and we're not auto-creating
					if not auto_create_attributes:
						frappe.logger("woo_prime").info(
							f"Skipping attribute '{attr_name}' - Woo Attribute not found. Create it manually in Woo Attribute list."
						)
					continue
				val_str = ", ".join(sorted(list(vals)))
				display_name = frappe.db.get_value("Woo Attribute", woo_attr, "attribute_name") if woo_attr else attr_name
				if not display_name:
					display_name = attr_name
				if woo_attr in existing_attrs or attr_name in existing_attrs:
					row = existing_attrs.get(woo_attr) or existing_attrs.get(attr_name)
					if row:
						row.attribute = woo_attr
						if not row.attribute_name:
							row.attribute_name = display_name
						if not row.options and val_str:
							row.options = val_str
				else:
					self.append("item_attributes", {
						"attribute": woo_attr,
						"attribute_name": display_name,
						"options": val_str,
						"is_variation": 1,
						"is_visible": 1,
					})
		else:
			# Simple product (no variants)
			for a in (item.attributes or []):
				if not a.attribute:
					continue
				woo_attr = _get_or_create_woo_attribute_doc(a.attribute, auto_create=auto_create_attributes)
				if not woo_attr:
					# Skip this attribute if Woo Attribute doesn't exist and we're not auto-creating
					if not auto_create_attributes:
						frappe.logger("woo_prime").info(
							f"Skipping attribute '{a.attribute}' - Woo Attribute not found. Create it manually in Woo Attribute list."
						)
					continue
				val = clean_text(a.attribute_value) if a.attribute_value else ""
				display_name = frappe.db.get_value("Woo Attribute", woo_attr, "attribute_name") if woo_attr else a.attribute
				if not display_name:
					display_name = a.attribute
				if woo_attr in existing_attrs or a.attribute in existing_attrs:
					row = existing_attrs.get(woo_attr) or existing_attrs.get(a.attribute)
					if row:
						row.attribute = woo_attr
						if not row.attribute_name:
							row.attribute_name = display_name
						if not row.options and val:
							row.options = val
				else:
					self.append("item_attributes", {
						"attribute": woo_attr,
						"attribute_name": display_name,
						"options": val,
						"is_variation": 0,
						"is_visible": 1,
					})

	@frappe.whitelist()
	def populate_attributes_from_erpnext(self, overwrite=False):
		"""Whitelisted method to reload attributes from ERPNext."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		overwrite = bool(cint(overwrite))
		self._load_attributes_from_erpnext(overwrite=overwrite)
		self.flags.ignore_permissions = True
		self.save(ignore_permissions=True)
		return {"status": "success", "count": len(self.item_attributes or [])}

	def after_insert(self):
		"""Clear tombstone when a Woo Item is explicitly created or restored (Item 2)."""
		try:
			from woo_prime.woo_prime.doctype.woo_ignored_item.woo_ignored_item import remove_tombstone
			remove_tombstone(item_code=self.item_code, sku=self.sku)
		except Exception:
			pass

	def on_update(self):
		"""Auto-sync to WooCommerce when Woo Item is updated."""
		if getattr(self.flags, "in_woo_sync", False) or getattr(frappe.flags, "in_test", False):
			return

		try:
			settings = frappe.get_single("Woo Settings")
			if not settings.enabled:
				return

			# If item is published and has a WooCommerce product ID, auto-sync changes in background
			if self.published and self.woo_product_id:
				doc_before = self.get_doc_before_save()
				changed_fields = _detect_changed_woo_fields(self, doc_before)
				if changed_fields is not None and len(changed_fields) == 0:
					return

				image_changed = bool(changed_fields and ("image" in changed_fields or "images" in changed_fields))
				_sfx = "img" if image_changed else "partial"

				if "item_attributes" in changed_fields or getattr(self, "has_variants", 0):
					frappe.enqueue(
						"woo_prime.woo_prime.doctype.woo_item.woo_item._publish_single_woo_item_worker",
						woo_item_name=self.name,
						user=frappe.session.user,
						queue="long",
						timeout=1800,
						enqueue_after_commit=True,
						job_id=f"woo_pub_{self.name}_attrs",
						deduplicate=True,
						sync_images=image_changed,
					)
				else:
					frappe.enqueue(
						"woo_prime.woo_prime.doctype.woo_item.woo_item._publish_partial_woo_item_worker",
						woo_item_name=self.name,
						changed_fields=changed_fields,
						user=frappe.session.user,
						queue="long",
						timeout=1800,
						enqueue_after_commit=True,
						job_id=f"woo_pub_{self.name}_{_sfx}",
						deduplicate=True,
					)
		except Exception as e:
			frappe.logger("woo_prime").error(f"Error enqueuing partial sync for {self.name}: {e}")

	@frappe.whitelist()
	def publish_to_woocommerce(self, background=True, sync_images=True):
		"""Create or update product on WooCommerce."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		background = _parse_bool(background)
		if sync_images is None:
			sync_images = True
		else:
			sync_images = _parse_bool(sync_images)

		if background:
			frappe.enqueue(
				"woo_prime.woo_prime.doctype.woo_item.woo_item._publish_single_woo_item_worker",
				woo_item_name=self.name,
				user=frappe.session.user,
				queue="long",
				timeout=1800,
				enqueue_after_commit=True,
				job_id=f"woo_publish_{self.name}",
				deduplicate=True,
				sync_images=sync_images,
			)
			msg = _("Publishing '{0}' to WooCommerce in the background...").format(self.item_name or self.name)
			return {"status": "queued", "message": msg, "woo_item_name": self.name}
		else:
			return self._execute_publish_sync(user=frappe.session.user, sync_images=sync_images)

	def _execute_publish_sync(self, user=None, sync_images=None):
		from woo_prime.api.sync import publish_item_to_woo
		from woo_prime.utils import safe_log_error

		if sync_images is not None:
			self.flags.sync_images = _parse_bool(sync_images)

		# Set in_woo_sync BEFORE publish so _auto_populate_attributes() runs during the sync
		self.flags.in_woo_sync = True

		try:
			result = publish_item_to_woo(self, sync_images=getattr(self.flags, "sync_images", None))
			woo_id = result.get("id") if isinstance(result, dict) else None
			if woo_id:
				self.woo_product_id = woo_id
				self.published = 1
				permalink = result.get("permalink", "")
				if permalink:
					self.woo_product_url = html.unescape(permalink)
				# Persist critical IDs immediately using db_set before full document save
				# This prevents losing woo_product_id if save() encounters a lock timeout (Item 5)
				try:
					self.db_set("woo_product_id", woo_id, update_modified=False)
					self.db_set("published", 1, update_modified=False)
					if self.woo_product_url:
						self.db_set("woo_product_url", self.woo_product_url, update_modified=False)
				except Exception as db_set_err:
					frappe.logger("woo_prime").debug(f"Immediate db_set notice: {db_set_err}")

			try:
				self.reload()
			except Exception:
				pass

			if woo_id:
				self.woo_product_id = woo_id
				self.published = 1
				permalink = result.get("permalink", "")
				if permalink:
					self.woo_product_url = html.unescape(permalink)

			# Keep the status computed by the inner publish (Item 4)
			inner_status = result.get("sync_status") if isinstance(result, dict) else None
			self.sync_status = inner_status or self.sync_status or "Synced"
			self.last_synced = now_datetime()
			self.flags.in_woo_sync = True
			self.flags.ignore_version = True

			# Save with retry on lock wait timeout (Item 5)
			for attempt in range(3):
				try:
					self.save(ignore_permissions=True)
					break
				except Exception as save_err:
					if ("1205" in str(save_err) or "Lock wait timeout" in str(save_err)) and attempt < 2:
						import time
						time.sleep(0.5 * (attempt + 1))
						try:
							self.reload()
						except Exception:
							pass
						self.woo_product_id = woo_id
						self.published = 1
						self.sync_status = inner_status or self.sync_status or "Synced"
						self.flags.in_woo_sync = True
						self.flags.ignore_version = True
					else:
						raise

			# Clear tombstone on explicit user publish (Item 2)
			try:
				from woo_prime.woo_prime.doctype.woo_ignored_item.woo_ignored_item import remove_tombstone
				remove_tombstone(item_code=self.item_code, sku=self.sku)
			except Exception:
				pass

			# Log outcome according to sync_status (Item 4)
			log_status = "Success"
			err_msg = None
			if self.sync_status == "Partial":
				log_status = "Warning"
				err_msg = (result.get("error_message") if isinstance(result, dict) else None) or f"Template '{self.item_name}' partially synced: {result.get('synced_variants_count', 0)}/{result.get('expected_variants_count', 0)} variation(s) synced."
			elif self.sync_status == "Error":
				log_status = "Failed"
				err_msg = (result.get("error_message") if isinstance(result, dict) else None)
				if not err_msg:
					if isinstance(result, dict) and result.get("expected_variants_count") == 0:
						err_msg = f"Product '{self.item_name}' published to WooCommerce (ID: {self.woo_product_id}), but no variants were synced."
					elif isinstance(result, dict) and result.get("failed_variants"):
						err_msg = f"Failed variants: {result.get('failed_variants')}"
					else:
						err_msg = f"Publish failed for '{self.item_name}'."

			create_sync_log(
				sync_type="Item",
				direction="Outgoing",
				status=log_status,
				reference_doctype="Woo Item",
				reference_name=self.name,
				woo_reference_id=str(self.woo_product_id or ""),
				request_data={"item_code": self.item_code, "sku": self.sku, "item_name": self.item_name},
				response_data=result if isinstance(result, dict) else {"woo_product_id": self.woo_product_id, "url": self.woo_product_url},
				error_message=err_msg,
			)

			synced_cnt = result.get("synced_variants_count") if isinstance(result, dict) else None
			exp_cnt = result.get("expected_variants_count") if isinstance(result, dict) else None

			if self.sync_status == "Partial":
				details = f"<br><br>{err_msg}" if err_msg else ""
				msg = _("⚠️ Template '{0}' published to WooCommerce (ID: {1}), but only {2}/{3} variants synced.{4}").format(
					self.item_name or self.name, self.woo_product_id, synced_cnt or 0, exp_cnt or 0, details
				)
			elif self.sync_status == "Error":
				details = f"<br><br>{err_msg}" if err_msg else ""
				msg = _("⚠️ Product '{0}' published to WooCommerce (ID: {1}), but no variants were synced.{2}").format(
					self.item_name or self.name, self.woo_product_id, details
				)
			else:
				msg = _("✅ Product '{0}' published to WooCommerce successfully!<br>Product ID: <b>{1}</b>").format(
					self.item_name or self.name, self.woo_product_id
				)

			_publish_item_progress(
				user=user,
				operation="publish_item",
				status="completed" if self.sync_status == "Synced" else "warning",
				message=msg,
				woo_item_name=self.name,
				item_code=self.item_code,
				item_name=self.item_name,
				woo_product_id=self.woo_product_id,
				woo_product_url=self.woo_product_url,
			)

			return {
				"status": "partial" if self.sync_status == "Partial" else ("error" if self.sync_status == "Error" else "success"),
				"sync_status": self.sync_status,
				"woo_product_id": self.woo_product_id,
				"woo_product_url": self.woo_product_url,
				"woo_item_name": self.name,
				"message": msg,
			}
		except Exception as e:
			try:
				self.reload()
			except Exception:
				pass
			self.sync_status = "Error"
			self.last_synced = now_datetime()
			self.flags.in_woo_sync = True
			self.save(ignore_permissions=True)

			create_sync_log(
				sync_type="Item",
				direction="Outgoing",
				status="Failed",
				reference_doctype="Woo Item",
				reference_name=self.name,
				woo_reference_id=str(self.woo_product_id or ""),
				request_data={"item_code": self.item_code, "sku": self.sku, "item_name": self.item_name},
				response_data={"error": str(e)},
				error_message=str(e),
			)
			safe_log_error(title="woo_prime: Publish Item Failed", message=frappe.get_traceback())

			err_msg = _("❌ Failed to publish '{0}' to WooCommerce: {1}").format(
				self.item_name or self.name, str(e)
			)

			_publish_item_progress(
				user=user,
				operation="publish_item",
				status="error",
				message=err_msg,
				woo_item_name=self.name,
				item_code=self.item_code,
				item_name=self.item_name,
			)

			raise

	@frappe.whitelist()
	def sync_item_now(self):
		"""Manually re-sync this item (details, stock, price) to WooCommerce without re-uploading images."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		return self._execute_publish_sync(user=frappe.session.user, sync_images=False)

	@frappe.whitelist()
	def sync_stock_now(self):
		"""Push current stock qty to WooCommerce."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		from woo_prime.api.sync import sync_stock_to_woo

		if not self.woo_product_id:
			self.validate()
			self.save(ignore_permissions=True)
			frappe.msgprint(
				_("✅ Regular Price ({0}) and Sale Price ({1}) refreshed from ERPNext.<br><small>(Note: Item not published to WooCommerce yet)</small>").format(
					self.regular_price or 0, self.sale_price or 0
				),
				title=_("Prices Refreshed"),
				indicator="blue",
			)
			return

		try:
			# If this is a template item, sync stock for all its variants ONLY
			has_variants = frappe.db.get_value("Item", self.item_code, "has_variants")
			if has_variants:
				# Template items: sync variants only (stock is managed at variation level)
				manage_var_stock = getattr(self, "manage_variation_stock", None)
				variant_names = frappe.get_all("Item", filters={"variant_of": self.item_code, "disabled": 0}, pluck="name")
				count = 0
				for v_name in variant_names:
					v_woo_name = frappe.db.get_value("Woo Item", {"item_code": v_name})
					if v_woo_name:
						v_woo_doc = frappe.get_doc("Woo Item", v_woo_name)
						if manage_var_stock is not None and v_woo_doc.manage_stock != manage_var_stock:
							v_woo_doc.manage_stock = int(manage_var_stock)
							v_woo_doc.db_set("manage_stock", int(manage_var_stock))
						try:
							sync_stock_to_woo(v_woo_doc)
							v_woo_doc.reload()
							if v_woo_doc.woo_product_id:
								v_woo_doc.db_set("last_synced", now_datetime())
								v_woo_doc.db_set("sync_status", "Synced")
								count += 1
						except Exception as var_stock_err:
							frappe.log_error(f"Error syncing stock for variant {v_name}: {var_stock_err}")
				self.last_synced = now_datetime()
				self.sync_status = "Synced"
				self.save()
				frappe.msgprint(
					_("✅ Stock synced to WooCommerce for {0} variant(s)!").format(count),
					title=_("Stock Synced"),
					indicator="green",
				)
				return

			# Simple or Variant items: sync own stock
			sync_stock_to_woo(self)
			self.last_synced = now_datetime()
			self.sync_status = "Synced"
			self.save()

			frappe.msgprint(
				_("✅ Stock synced to WooCommerce successfully!"),
				title=_("Stock Synced"),
				indicator="green",
			)
		except Exception as e:
			create_sync_log(
				sync_type="Stock",
				direction="Outgoing",
				status="Failed",
				reference_doctype="Woo Item",
				reference_name=self.name,
				woo_reference_id=str(self.woo_product_id or ""),
				error_message=str(e),
			)
			frappe.throw(_("Stock sync failed: {0}").format(str(e)))

	@frappe.whitelist()
	def sync_price_now(self):
		"""Push price to WooCommerce."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		from woo_prime.api.sync import sync_price_to_woo

		if not self.woo_product_id:
			frappe.throw(_("This item has not been published to WooCommerce yet."))

		try:
			# If this is a template item, sync prices for all its variants ONLY
			has_variants = frappe.db.get_value("Item", self.item_code, "has_variants")
			if has_variants:
				# Template items: sync variant prices only (price is managed at variation level)
				variant_names = frappe.get_all("Item", filters={"variant_of": self.item_code, "disabled": 0}, pluck="name")
				count = 0
				for v_name in variant_names:
					v_woo_name = frappe.db.get_value("Woo Item", {"item_code": v_name})
					if v_woo_name:
						v_woo_doc = frappe.get_doc("Woo Item", v_woo_name)
						try:
							sync_price_to_woo(v_woo_doc)
							v_woo_doc.reload()
							if v_woo_doc.woo_product_id:
								v_woo_doc.db_set("last_synced", now_datetime())
								v_woo_doc.db_set("sync_status", "Synced")
								count += 1
						except Exception as var_price_err:
							frappe.log_error(f"Error syncing price for variant {v_name}: {var_price_err}")
				self.last_synced = now_datetime()
				self.sync_status = "Synced"
				self.save()
				frappe.msgprint(
					_("✅ Price synced to WooCommerce for {0} variant(s)!").format(count),
					title=_("Price Synced"),
					indicator="green",
				)
				return

			# Simple or Variant items: sync own price
			sync_price_to_woo(self)
			self.reload()
			self.last_synced = now_datetime()
			self.sync_status = "Synced"
			self.save()

			sale_text = f"<br>Sale Price: <b>{self.sale_price}</b>" if getattr(self, "sale_price", None) and flt(self.sale_price) > 0 else ""
			frappe.msgprint(
				_("✅ Price synced to WooCommerce successfully!<br>Regular Price: <b>{0}</b>{1}").format(
					self.regular_price or 0, sale_text
				),
				title=_("Price Synced"),
				indicator="green",
			)
		except Exception as e:
			create_sync_log(
				sync_type="Price",
				direction="Outgoing",
				status="Failed",
				reference_doctype="Woo Item",
				reference_name=self.name,
				woo_reference_id=str(self.woo_product_id or ""),
				error_message=str(e),
			)
			frappe.throw(_("Price sync failed: {0}").format(str(e)))

	@frappe.whitelist()
	def fetch_price_from_erpnext(self):
		"""Fetch and apply regular_price and Pricing Rule sale_price directly to this Woo Item."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		if not self.item_code:
			frappe.throw(_("Please select an Item Code first."))

		# If template item, also fetch prices for all active variants
		has_variants = frappe.db.get_value("Item", self.item_code, "has_variants")
		if has_variants:
			variant_names = frappe.get_all("Item", filters={"variant_of": self.item_code, "disabled": 0}, pluck="name")
			for v_name in variant_names:
				v_woo_name = frappe.db.get_value("Woo Item", {"item_code": v_name})
				if v_woo_name:
					try:
						v_doc = frappe.get_doc("Woo Item", v_woo_name)
						v_doc.fetch_price_from_erpnext()
					except Exception as var_fetch_err:
						frappe.log_error(f"Error fetching price for variant {v_name}: {var_fetch_err}")

		settings = frappe.get_single("Woo Settings")
		from woo_prime.api.sync import _get_item_price, _resolve_price_list
		from frappe.utils import flt

		price_list = _resolve_price_list(settings)
		regular_price = _get_item_price(self.item_code, price_list)
		if not regular_price or flt(regular_price) <= 0:
			variant_of = frappe.db.get_value("Item", self.item_code, "variant_of")
			if variant_of:
				parent_price = _get_item_price(variant_of, price_list)
				if parent_price and flt(parent_price) > 0:
					regular_price = flt(parent_price)
				else:
					parent_woo_price = frappe.db.get_value("Woo Item", {"item_code": variant_of}, "regular_price")
					if parent_woo_price and flt(parent_woo_price) > 0:
						regular_price = flt(parent_woo_price)
		if not regular_price or flt(regular_price) <= 0:
			regular_price = flt(self.regular_price) if self.regular_price else 0.0

		sale_price = 0.0
		rule_name = None
		skip_reason = None
		if regular_price > 0:
			try:
				from woo_prime.api.sale_price_sync import _get_sale_price_for_item
				calc_sale, matched_rule, reason = _get_sale_price_for_item(self.item_code, regular_price, settings)
				if calc_sale is not None and flt(calc_sale) > 0:
					sale_price = flt(calc_sale)
					rule_name = getattr(matched_rule, "name", str(matched_rule)) if matched_rule else None
				else:
					skip_reason = reason
			except Exception as e:
				frappe.log_error(f"Error fetching price from ERPNext for {self.name}: {e}")

		if regular_price > 0:
			self.regular_price = flt(regular_price)
		self.sale_price = flt(sale_price)

		if not self.is_new():
			self.db_set("regular_price", self.regular_price)
			self.db_set("sale_price", self.sale_price)

		return {
			"regular_price": self.regular_price,
			"sale_price": self.sale_price,
			"pricing_rule": rule_name,
			"reason": skip_reason,
		}


@frappe.whitelist()
def publish_single_item(woo_item_name, background=False, sync_images=True):
	"""Standalone whitelist method to publish a Woo Item without for_update lock."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	background = _parse_bool(background)
	if sync_images is None:
		sync_images = True
	else:
		sync_images = _parse_bool(sync_images)
	if not woo_item_name or not frappe.db.exists("Woo Item", woo_item_name):
		frappe.throw(_("Woo Item {0} not found.").format(woo_item_name))

	doc = frappe.get_doc("Woo Item", woo_item_name)
	return doc.publish_to_woocommerce(background=background, sync_images=sync_images)


@frappe.whitelist()
def test_publish(woo_item_name="TP00210"):
	import traceback
	try:
		doc = frappe.get_doc("Woo Item", woo_item_name)
		res = doc._execute_publish_sync(user="Administrator")
		print("PUBLISH SUCCESS:", res)
		return res
	except Exception as e:
		traceback.print_exc()
		return {"error": str(e), "traceback": traceback.format_exc()}


# -------------------------------------------------------------------------
# Description & Table Formatting Helpers
# -------------------------------------------------------------------------

def clean_table_html_for_editor(content):
	"""Normalize HTML tables for Frappe Text Editor (Quill) and WooCommerce display.

	Quill does not natively support <thead> and <th>. It collapses all <th> elements
	into the first <td> of the row, breaking table layouts (e.g. Size Charts).
	This converts <th> to bold <td> and standardizes table structure into clean <tbody>.
	"""
	if not content or "<table" not in str(content).lower():
		return content

	res = str(content)

	# 1. Strip thead / tfoot tags
	res = re.sub(r"</?thead[^>]*>", "", res, flags=re.I)
	res = re.sub(r"</?tfoot[^>]*>", "", res, flags=re.I)

	# 2. Replace <th ...>...</th> with <td ...><strong>...</strong></td>
	def th_to_td(m):
		attrs = m.group(1) or ""
		inner = (m.group(2) or "").strip()
		style_match = re.search(r"style=[\"\x27](.*?)[\"\x27]", attrs, re.I)
		if style_match:
			style = style_match.group(1).rstrip("; ")
			if "font-weight" not in style.lower():
				style += ";font-weight:bold"
			if "background" not in style.lower():
				style += ";background-color:#f8fafc"
			if "text-align" not in style.lower():
				style += ";text-align:center"
			attrs = attrs[:style_match.start(1)] + style + attrs[style_match.end(1):]
		else:
			attrs += ' style="font-weight:bold;background-color:#f8fafc;text-align:center;border:1px solid #cbd5e1;padding:7px 4px;"'
		return f"<td{attrs}><strong>{inner}</strong></td>"

	res = re.sub(r"<th(\s[^>]*)?>(.*?)</th>", th_to_td, res, flags=re.I | re.S)

	# 3. Ensure all <tr> in table are wrapped cleanly in <tbody>
	def wrap_tbody(m):
		table_open = m.group(1)
		body = m.group(2)
		table_close = m.group(3)
		body_clean = re.sub(r"</?tbody[^>]*>", "", body, flags=re.I).strip()
		return f"{table_open}<tbody>\n{body_clean}\n</tbody>{table_close}"

	res = re.sub(r"(<table[^>]*>)(.*?)(</table>)", wrap_tbody, res, flags=re.I | re.S)
	return res


@frappe.whitelist()
def fix_existing_table_descriptions(woo_item_name=None):
	"""Clean and fix table layout in woo_description for one or all Woo Items."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	filters = {"name": woo_item_name} if woo_item_name else {}
	items = frappe.get_all("Woo Item", filters=filters, fields=["name", "item_code", "woo_description"])
	count = 0
	for it in items:
		desc = it.woo_description
		# If description has broken table or <th>, or if empty and item_code exists
		if not desc and it.item_code:
			raw = frappe.db.get_value("Item", it.item_code, "description")
			if raw:
				desc = html.unescape(str(raw))
		elif it.item_code:
			# Re-fetch from Item Master if user wants pristine layout or if table header was mangled
			raw = frappe.db.get_value("Item", it.item_code, "description")
			if raw and ("<th" in str(raw).lower() or "<th" in str(desc).lower() or "BODY LENGTH SLEEVE" in str(desc) or "LENGTH WIDTH" in str(desc)):
				desc = html.unescape(str(raw))

		if desc:
			cleaned = clean_table_html_for_editor(desc)
			if cleaned != it.woo_description:
				frappe.db.set_value("Woo Item", it.name, "woo_description", cleaned)
				count += 1

	frappe.db.commit()
	return {"status": "success", "updated_count": count}


# -------------------------------------------------------------------------
# Attribute & Term Helpers
# -------------------------------------------------------------------------

def _get_or_create_woo_attribute_doc(attr_name, auto_create=False):
	"""Find or optionally create Woo Attribute for the given attribute name.

	Args:
		attr_name: Attribute name to search for
		auto_create: If True, create Woo Attribute if not found. Default False.

	Returns the Woo Attribute name (str) or None if it could not be found or created.
	"""
	clean = clean_text(attr_name)
	woo_attr_name = (
		frappe.db.get_value("Woo Attribute", {"attribute_name": clean})
		or frappe.db.get_value("Woo Attribute", {"erpnext_item_attribute": clean})
		or (frappe.db.exists("Woo Attribute", clean) and clean)
	)
	
	if not woo_attr_name and auto_create:
		try:
			wa = frappe.new_doc("Woo Attribute")
			wa.attribute_name = clean
			wa.erpnext_item_attribute = clean if frappe.db.exists("Item Attribute", clean) else None
			wa.slug = frappe.scrub(clean).replace("_", "-")
			wa.type = "select"
			wa.flags.ignore_permissions = True
			wa.save(ignore_permissions=True)
			woo_attr_name = wa.name
		except frappe.DuplicateEntryError:
			# Another request created it concurrently — re-fetch
			woo_attr_name = (
				frappe.db.get_value("Woo Attribute", {"attribute_name": clean})
				or frappe.db.get_value("Woo Attribute", {"erpnext_item_attribute": clean})
				or (frappe.db.exists("Woo Attribute", clean) and clean)
			)
			if not woo_attr_name:
				frappe.logger("woo_prime").warning(
					f"_get_or_create_woo_attribute_doc: could not find or create Woo Attribute for '{clean}'"
				)
				return None
		except Exception:
			frappe.logger("woo_prime").warning(
				f"_get_or_create_woo_attribute_doc: failed to create Woo Attribute for '{clean}'"
			)
			return None
	
	return woo_attr_name


@frappe.whitelist()
def get_attribute_terms_for_woo_item(woo_attribute, item_code=None):
	"""Return available terms/values for a Woo Attribute to populate multi-select.
	Supports both regular (dropdown) and numeric range ERPNext Item Attributes.
	"""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	terms = set()

	if not woo_attribute:
		return {"terms": []}

	clean_attr = clean_text(woo_attribute)

	def _add_values_from_erp_attr(erp_attr_name):
		"""Add values from an ERPNext Item Attribute (regular or numeric)."""
		if not erp_attr_name or not frappe.db.exists("Item Attribute", erp_attr_name):
			return
		try:
			from woo_prime.woo_prime.doctype.woo_attribute.woo_attribute import _get_item_attribute_values
			for v in _get_item_attribute_values(erp_attr_name):
				if v:
					terms.add(clean_text(str(v)))
		except Exception:
			# Fallback: direct doc read
			try:
				ia = frappe.get_doc("Item Attribute", erp_attr_name)
				for val in (ia.item_attribute_values or []):
					if val.attribute_value:
						terms.add(clean_text(val.attribute_value))
			except Exception:
				pass

	# 1. From Woo Attribute term_swatches & linked ERPNext Item Attribute
	wa_name = (
		frappe.db.get_value("Woo Attribute", {"attribute_name": clean_attr})
		or (frappe.db.exists("Woo Attribute", clean_attr) and clean_attr)
		or (frappe.db.exists("Woo Attribute", woo_attribute) and woo_attribute)
	)
	if wa_name:
		try:
			wa = frappe.get_doc("Woo Attribute", wa_name)
			for t in (wa.term_swatches or []):
				if t.term_name:
					terms.add(clean_text(t.term_name))

			erp_attr = wa.erpnext_item_attribute or wa.attribute_name
			_add_values_from_erp_attr(erp_attr)
		except Exception:
			pass

	# 2. Directly from ERPNext Item Attribute if exists
	for possible_name in [clean_attr, woo_attribute]:
		_add_values_from_erp_attr(possible_name)

	# 3. If item_code provided, inspect its variants (always pulls actual used values)
	if item_code and frappe.db.exists("Item", item_code):
		try:
			item = frappe.get_doc("Item", item_code)
			if item.has_variants:
				variant_names = frappe.get_all("Item", filters={"variant_of": item.name, "disabled": 0}, pluck="name")
				for v_name in variant_names:
					v_doc = frappe.get_doc("Item", v_name)
					for a in (v_doc.attributes or []):
						if a.attribute and (
							clean_text(a.attribute).lower() == clean_attr.lower()
							or clean_text(a.attribute).lower() == clean_text(woo_attribute).lower()
						):
							if a.attribute_value:
								terms.add(clean_text(a.attribute_value))
		except Exception:
			pass

	# Bug #8 fix: deduplicate case-insensitively (keep original casing of first seen value)
	seen_lower = set()
	deduped = []
	# Sort numerically if all values look like numbers, else alphabetically
	try:
		sorted_terms = sorted(list(terms), key=lambda x: float(x))
	except Exception:
		sorted_terms = sorted(list(terms))
	for t in sorted_terms:
		if t.lower() not in seen_lower:
			seen_lower.add(t.lower())
			deduped.append(t)
	return {"terms": deduped}


@frappe.whitelist()
def get_all_attribute_terms_for_woo_item(attributes=None, item_code=None):
	"""Return dict of {attribute_name: [terms]} for multiple attributes."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	import json
	if isinstance(attributes, str):
		try:
			attributes = json.loads(attributes)
		except Exception:
			attributes = [a.strip() for a in attributes.split(",") if a.strip()]
	attributes = attributes or []
	result = {}
	for attr in attributes:
		if attr:
			r = get_attribute_terms_for_woo_item(attr, item_code=item_code)
			result[attr] = r.get("terms", [])
	return {"attribute_terms": result}


def create_sync_log(**kwargs):
	"""Helper to create a Woo Sync Log entry."""
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
	return create_log(**kwargs)


@frappe.whitelist()
def bulk_publish(items, background=True):
	"""Bulk publish multiple Woo Items to WooCommerce."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	import json

	if isinstance(items, str):
		items = json.loads(items)

	background = _parse_bool(background)

	if background:
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_item.woo_item._bulk_publish_worker",
			items=items,
			user=frappe.session.user,
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
			job_name=f"woo_bulk_publish_{frappe.session.user}",
		)
		msg = _("Started background publish for {0} item(s). You can monitor progress.").format(len(items))
		if frappe.request:
			frappe.msgprint(msg, title=_("Bulk Publish Queued"), indicator="blue")
		return {"status": "queued", "message": msg, "total": len(items)}
	else:
		return _bulk_publish_worker(items=items, user=frappe.session.user)


def _publish_single_woo_item_worker(woo_item_name, user=None, sync_images=True):
	"""Background worker: publish a single Woo Item to WooCommerce."""
	try:
		if not frappe.db.exists("Woo Item", woo_item_name):
			return
		woo_item = frappe.get_doc("Woo Item", woo_item_name)
		if sync_images is None:
			sync_images = True
		else:
			sync_images = _parse_bool(sync_images)
		woo_item.flags.sync_images = sync_images
		_publish_item_progress(
			user=user,
			operation="publish_item",
			status="processing",
			message=_("Publishing '{0}' to WooCommerce...").format(woo_item.item_name or woo_item.name),
			woo_item_name=woo_item.name,
			item_code=woo_item.item_code,
			item_name=woo_item.item_name,
		)
		woo_item._execute_publish_sync(user=user, sync_images=sync_images)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		frappe.logger("woo_prime").error(f"Background publish failed for {woo_item_name}: {e}")
		# After rollback, persist Error status in a fresh transaction so user sees it on form
		try:
			frappe.db.set_value("Woo Item", woo_item_name, {
				"sync_status": "Error",
				"last_synced": now_datetime(),
			})
			frappe.db.commit()
		except Exception:
			pass


def _detect_changed_woo_fields(doc, doc_before):
	"""Compare doc and doc_before to find which WooCommerce-relevant fields changed.
	Returns list of changed field names, or None if doc_before is not available.
	"""
	if not doc_before:
		return None

	relevant_scalars = [
		"item_name",
		"regular_price",
		"sale_price",
		"woo_description",
		"woo_short_description",
		"manage_stock",
		"image",
		"woo_featured",
		"catalog_visibility",
		"allow_backorders",
		"purchase_note",
		"menu_order",
		"woo_weight",
		"woo_length",
		"woo_width",
		"woo_height",
		"woo_virtual",
		"woo_downloadable",
		"reviews_allowed",
	]

	changed = []
	for f in relevant_scalars:
		val_curr = getattr(doc, f, None)
		val_prev = getattr(doc_before, f, None)
		if f in ("regular_price", "sale_price", "woo_weight", "woo_length", "woo_width", "woo_height"):
			if flt(val_curr) != flt(val_prev):
				changed.append(f)
		elif f in ("manage_stock", "woo_featured", "woo_virtual", "woo_downloadable", "reviews_allowed", "menu_order"):
			if cint(val_curr) != cint(val_prev):
				changed.append(f)
		else:
			if (val_curr or "") != (val_prev or ""):
				changed.append(f)

	# Compare child table `categories`
	curr_cats = sorted([r.category for r in (getattr(doc, "categories", []) or []) if getattr(r, "category", None)])
	prev_cats = sorted([r.category for r in (getattr(doc_before, "categories", []) or []) if getattr(r, "category", None)])
	if curr_cats != prev_cats:
		changed.append("categories")

	# Compare child table `woo_tags`
	curr_tags = sorted([r.tag for r in (getattr(doc, "woo_tags", []) or []) if getattr(r, "tag", None)])
	prev_tags = sorted([r.tag for r in (getattr(doc_before, "woo_tags", []) or []) if getattr(r, "tag", None)])
	if curr_tags != prev_tags:
		changed.append("woo_tags")

	# Compare child table `images`
	curr_imgs = [getattr(r, "image", None) for r in (getattr(doc, "images", []) or [])]
	prev_imgs = [getattr(r, "image", None) for r in (getattr(doc_before, "images", []) or [])]
	if curr_imgs != prev_imgs:
		changed.append("images")

	# Compare child table `item_attributes`
	curr_attrs = [
		(r.attribute or "", r.attribute_name or "", (r.options or "").strip(), cint(getattr(r, "is_variation", 1)), cint(getattr(r, "is_visible", 1)))
		for r in (getattr(doc, "item_attributes", []) or [])
	]
	prev_attrs = [
		(r.attribute or "", r.attribute_name or "", (r.options or "").strip(), cint(getattr(r, "is_variation", 1)), cint(getattr(r, "is_visible", 1)))
		for r in (getattr(doc_before, "item_attributes", []) or [])
	]
	if curr_attrs != prev_attrs:
		changed.append("item_attributes")

	return changed


def _publish_partial_woo_item_worker(woo_item_name, changed_fields=None, user=None):
	"""Background worker: partially publish only changed fields to WooCommerce."""
	try:
		if not frappe.db.exists("Woo Item", woo_item_name):
			return
		woo_item = frappe.get_doc("Woo Item", woo_item_name)
		if not woo_item.published or not woo_item.woo_product_id:
			# Not published yet, do normal full publish
			woo_item._execute_publish_sync(user=user, sync_images=True)
			frappe.db.commit()
			return

		if not changed_fields or "item_attributes" in changed_fields or getattr(woo_item, "has_variants", 0):
			# If item_attributes changed or template has_variants: full publish sync without re-uploading images
			woo_item._execute_publish_sync(user=user, sync_images=False)
			frappe.db.commit()
			return

		from woo_prime.api.sync import sync_partial_item_to_woo
		res = sync_partial_item_to_woo(woo_item, changed_fields)
		if res:
			woo_item.db_set("last_synced", now_datetime(), update_modified=False)
			woo_item.db_set("sync_status", "Synced", update_modified=False)
			frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		frappe.logger("woo_prime").error(f"Partial background publish failed for {woo_item_name}: {e}")
		try:
			frappe.db.set_value("Woo Item", woo_item_name, {
				"sync_status": "Error",
				"last_synced": now_datetime(),
			})
			frappe.db.commit()
		except Exception:
			pass


def _bulk_publish_worker(items, user=None):
	"""Background worker: bulk publish multiple Woo Items to WooCommerce."""
	total = len(items)
	success_count = 0
	fail_count = 0

	_publish_item_progress(
		user=user,
		operation="bulk_publish",
		status="starting",
		message=_("Starting bulk publish of {0} item(s)...").format(total),
		total=total,
		current=0,
		success=0,
		failed=0,
	)

	for idx, item_name in enumerate(items):
		try:
			_publish_item_progress(
				user=user,
				operation="bulk_publish",
				status="processing",
				message=_("Publishing {0} ({1}/{2})...").format(item_name, idx + 1, total),
				total=total,
				current=idx + 1,
				success=success_count,
				failed=fail_count,
			)
			if frappe.db.exists("Woo Item", item_name):
				woo_item = frappe.get_doc("Woo Item", item_name)
				woo_item._execute_publish_sync(user=user)
				success_count += 1
				frappe.db.commit()
			else:
				fail_count += 1
		except Exception as e:
			fail_count += 1
			frappe.db.rollback()
			frappe.logger("woo_prime").error(f"Bulk publish error for {item_name}: {e}")

	final_msg = _("✅ Bulk publish completed: {0} succeeded, {1} failed.").format(success_count, fail_count)
	_publish_item_progress(
		user=user,
		operation="bulk_publish",
		status="completed",
		message=final_msg,
		total=total,
		current=total,
		success=success_count,
		failed=fail_count,
	)

	return {"success": success_count, "failed": fail_count, "message": final_msg}


def _publish_item_progress(user=None, **kwargs):
	"""Publish realtime progress message for item publish operations."""
	payload = dict(kwargs)
	if user:
		frappe.publish_realtime("woo_publish_progress", payload, user=user)
	else:
		frappe.publish_realtime("woo_publish_progress", payload)


def _parse_bool(value):
	"""Parse various boolean representations to Python bool.
	
	Converts string representations like "true", "1", "yes" to True,
	and other values to False. Handles direct boolean values and None.
	
	Args:
		value: Input value to parse (bool, str, int, or None)
	
	Returns:
		bool: Parsed boolean value
	
	Examples:
		>>> _parse_bool(True)
		True
		>>> _parse_bool("true")
		True
		>>> _parse_bool("1")
		True
		>>> _parse_bool("yes")
		True
		>>> _parse_bool("false")
		False
		>>> _parse_bool("")
		False
	"""
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.lower() in ("true", "1", "yes")
	return bool(value)


def _parse_int(value, default=10):
	"""Parse integer value with default fallback.
	
	Attempts to convert the input value to an integer. If conversion fails
	(ValueError or TypeError), returns the specified default value.
	
	Args:
		value: Input value to parse (int, str, or other)
		default (int): Default value to return on parsing failure (default: 10)
	
	Returns:
		int: Parsed integer or default value
	
	Examples:
		>>> _parse_int(100)
		100
		>>> _parse_int("50")
		50
		>>> _parse_int("invalid", default=20)
		20
		>>> _parse_int(None, default=15)
		15
	"""
	try:
		return int(value)
	except (ValueError, TypeError):
		return default




def _is_within_sync_window(now_val=None, start_val=None, end_val=None):
	"""Check if current time is within the configured auto sync window.

	Args:
		now_val: Optional override for now (datetime.time, timedelta, or str)
		start_val: Optional override for start (str, timedelta, or time)
		end_val: Optional override for end (str, timedelta, or time)

	Returns:
		bool: True if within sync window or schedule is disabled, False otherwise

	Note:
		- If enable_auto_sync_schedule is False, always returns True (no restriction)
		- If times are not configured, always returns True
		- Uses frappe.utils.get_time() for start/end and now_datetime().time() for now
	"""
	from datetime import datetime, timedelta, time as dt_time
	from frappe.utils import get_time, now_datetime

	def _normalize_time(val) -> dt_time | None:
		if val is None:
			return None
		if isinstance(val, dt_time):
			return val
		if isinstance(val, timedelta):
			return (datetime.min + val).time()
		try:
			t = get_time(val)
		except Exception:
			try:
				return datetime.strptime(str(val), "%H:%M:%S").time()
			except Exception:
				return None
		if isinstance(t, dt_time):
			return t
		if isinstance(t, timedelta):
			return (datetime.min + t).time()
		if isinstance(t, datetime):
			return t.time()
		try:
			return datetime.strptime(str(t), "%H:%M:%S").time()
		except Exception:
			return None

	if start_val is not None and end_val is not None:
		start_time = _normalize_time(start_val)
		end_time = _normalize_time(end_val)
	else:
		settings = frappe.get_single("Woo Settings")

		# If schedule is not enabled, allow sync anytime
		if not getattr(settings, "enable_auto_sync_schedule", 0):
			return True

		raw_start = getattr(settings, "auto_sync_start_time", None)
		raw_end = getattr(settings, "auto_sync_end_time", None)

		# If times not configured, allow sync anytime
		if not raw_start or not raw_end:
			return True

		start_time = _normalize_time(raw_start)
		end_time = _normalize_time(raw_end)

	if start_time is None or end_time is None:
		return True

	if now_val is not None:
		now = _normalize_time(now_val)
	else:
		now = now_datetime().time()

	if now is None:
		return True

	# Handle overnight windows (e.g., 22:00 to 06:00)
	if start_time <= end_time:
		# Normal case: start <= end (e.g., 02:00 to 06:00)
		within_window = start_time <= now <= end_time
	else:
		# Overnight case: start > end (e.g., 22:00 to 06:00)
		within_window = now >= start_time or now <= end_time

	if not within_window:
		logger = frappe.logger("woo_prime", allow_site=True, max_size=10_000_000, file_count=50)
		logger.info(
			f"[SyncWindow] Outside sync window (current: {now.strftime('%H:%M:%S')}, "
			f"window: {start_time.strftime('%H:%M:%S')} - {end_time.strftime('%H:%M:%S')}). "
			f"Skipping auto sync."
		)

	return within_window

def _publish_progress(user, operation, current_page, total_fetched, linked, created, skipped, failed, status, message):
	"""Publish real-time progress update for product fetch operations.
	
	Sends a realtime notification via Frappe's publish_realtime to update
	the user interface with current progress of the background job. The
	notification is sent immediately (after_commit=False) to provide
	responsive feedback.
	
	Args:
		user (str): Target user to receive the notification
		operation (str): Operation identifier ("fetch_products")
		current_page (int): Current page number being processed
		total_fetched (int): Total number of products fetched so far
		linked (int): Total number of products auto-linked to ERPNext Items
		created (int): Total number of new ERPNext Items created
		skipped (int): Total number of products skipped (e.g., empty products)
		failed (int): Total number of products that failed processing
		status (str): Current status ("starting", "fetching", "in_progress", "completed", "error")
		message (str): Human-readable status message for display
	
	Returns:
		None
	
	Note:
		Exceptions during publish are caught and logged to prevent
		disruption of the main background job execution.
	"""
	if not user:
		return
	
	try:
		frappe.publish_realtime(
			event="woo_fetch_progress",
			message={
				"operation": operation,
				"current_page": current_page,
				"total_fetched": total_fetched,
				"linked": linked,
				"created": created,
				"skipped": skipped,
				"failed": failed,
				"status": status,
				"message": message,
				"timestamp": frappe.utils.now()
			},
			user=user,
			after_commit=False
		)
	except Exception as e:
		frappe.logger("woo_prime").error(f"Failed to publish progress: {e}")


def _process_single_product(prod, settings, default_item_group, auto_create_missing, logger):
	"""Process a single product from WooCommerce response.
	
	Creates or updates a Woo Item record and optionally auto-links or creates
	the corresponding ERPNext Item. This function encapsulates the matching
	and linking logic for a single product.
	
	Args:
		prod (dict): Product data from WooCommerce API response
		settings (Document): Woo Settings singleton document
		default_item_group (str): Default Item Group name for new ERPNext Items
		auto_create_missing (bool): Whether to auto-create ERPNext Items for unmatched products
		logger (Logger): Frappe logger instance for logging operations
	
	Returns:
		dict: Result dictionary with keys:
			- "linked" (bool): True if Woo Item was linked to an ERPNext Item
			- "created" (bool): True if a new ERPNext Item was created
	
	Matching Logic:
		1. Find existing Woo Item by: woo_product_id, then sku, then primary key name
		2. Auto-link to ERPNext Item by: item_code match, then name match, then item_name match
		3. If matched: set item_code, set sync_status="Synced"
		4. If not matched and auto_create_missing=False: create new ERPNext Item
		5. If not matched and auto_create_missing=False: set sync_status="Not Synced"
	
	Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 10.1, 10.2, 10.3
	"""
	from frappe.utils import flt
	
	# Extract product data
	woo_id = prod.get("id")

	sku = (prod.get("sku") or "").strip()
	name = html.unescape(prod.get("name") or "")
	permalink = prod.get("permalink", "")
	description = html.unescape(prod.get("description", "") or "")
	short_description = html.unescape(prod.get("short_description", "") or "")
	
	# Generate Fake_SKU if SKU is empty (Requirement 8.7)
	if not sku:
		sku = f"WC-{woo_id}"
	
	# Find existing Woo Item (Requirements 10.1, 10.2)
	# Priority: woo_product_id, then sku field, then primary key name
	existing_name = (
		frappe.db.get_value("Woo Item", {"woo_product_id": woo_id})
		or frappe.db.get_value("Woo Item", {"sku": sku})
		or (frappe.db.exists("Woo Item", sku) and sku)
	)
	
	if existing_name:
		woo_item = frappe.get_doc("Woo Item", existing_name)
	else:
		woo_item = frappe.new_doc("Woo Item")
		woo_item.sku = sku
	
	# Set Woo Item fields (Requirements 8.1, 8.2, 8.6)
	woo_item.woo_product_id = woo_id
	woo_item.woo_product_url = html.unescape(permalink) if permalink else ""
	woo_item.woo_description = description
	woo_item.woo_short_description = short_description
	woo_item.published = 1
	if "manage_stock" in prod:
		woo_item.manage_stock = 1 if prod.get("manage_stock") else 0

	wc_type = prod.get("type")
	if wc_type == "simple" and not woo_item.woo_product_type:
		woo_item.woo_product_type = "Simple Item"
	elif wc_type == "variable" and not woo_item.woo_product_type:
		woo_item.woo_product_type = "Variant Item"
	
	# Set prices using frappe.utils.flt() (Requirement 8.2)
	reg_p = flt(prod.get("regular_price") or 0)
	sale_p = flt(prod.get("sale_price") or 0)
	if reg_p > 0:
		woo_item.regular_price = reg_p
	if sale_p > 0:
		woo_item.sale_price = sale_p

	# Set images (Primary + Gallery) from WooCommerce
	images = prod.get("images")
	if images and isinstance(images, list) and len(images) > 0:
		# 1. Main / Featured Image
		primary_img = images[0].get("src")
		if primary_img:
			woo_item.image = primary_img
			if images[0].get("id"):
				try:
					from woo_prime.api.sync import _cache_wp_media_id
					_cache_wp_media_id(primary_img, images[0]["id"])
				except Exception:
					pass

		# 2. Gallery Images (images[1:]) into child table `images`
		woo_item.set("images", [])
		for gal in images[1:]:
			src = gal.get("src")
			if src:
				woo_item.append("images", {
					"image": src,
					"caption": gal.get("alt") or gal.get("name") or "",
				})
				if gal.get("id"):
					try:
						from woo_prime.api.sync import _cache_wp_media_id
						_cache_wp_media_id(src, gal["id"])
					except Exception:
						pass
	elif prod.get("image") and isinstance(prod.get("image"), dict) and prod["image"].get("src"):
		woo_item.image = prod["image"]["src"]
		if prod["image"].get("id"):
			try:
				from woo_prime.api.sync import _cache_wp_media_id
				_cache_wp_media_id(prod["image"]["src"], prod["image"]["id"])
			except Exception:
				pass
	
	# Auto-link to ERPNext Item (Requirements 8.3, 8.4)
	# Priority: woo_product_id → item_code/SKU match → exact name match → item_name (only if unique)
	matched_item = (
		frappe.db.get_value("Item", {"item_code": sku}, "name")
		or frappe.db.get_value("Item", {"name": sku}, "name")
	)

	# item_name match only if exactly one Item matches (to avoid false positives)
	if not matched_item and name:
		name_matches = frappe.db.get_all("Item", {"item_name": name}, pluck="name", limit=2)
		if len(name_matches) == 1:
			matched_item = name_matches[0]
	
	linked = False
	created = False
	
	if matched_item:
		# Item found: link it (Requirement 8.4)
		woo_item.item_code = matched_item
		woo_item.sync_status = "Synced"
		linked = True
	else:
		# No match found
		if auto_create_missing and not frappe.db.exists("Item", sku):
			# Create new ERPNext Item (Requirement 8.5)
			new_item = frappe.new_doc("Item")
			new_item.item_code = sku
			new_item.item_name = name or sku
			new_item.item_group = default_item_group
			new_item.stock_uom = "Nos"
			new_item.is_stock_item = 1
			if description:
				new_item.description = description
			new_item.insert(ignore_permissions=True)
			
			# Link to newly created Item (Requirement 8.5)
			woo_item.item_code = new_item.name
			woo_item.sync_status = "Synced"
			created = True
			linked = True
		elif not woo_item.item_code:
			# Not auto-creating and no match (Requirement 8.4)
			woo_item.sync_status = "Not Synced"
	
	# Save Woo Item (Requirement 8.2) — set flag to prevent on_update re-enqueue
	woo_item.flags.in_woo_sync = True
	woo_item.save(ignore_permissions=True)

	# Attach WooCommerce tags to ERPNext Item
	prod_tags = prod.get("tags", [])
	target_item = woo_item.item_code
	if prod_tags and target_item:
		for t in prod_tags:
			t_name = clean_text(t.get("name"))
			if t_name:
				try:
					from frappe.desk.doctype.tag.tag import add_tag
					add_tag(t_name, "Item", target_item)
				except Exception:
					pass

	return {"linked": linked, "created": created}


def _process_single_variation(var, parent_id, settings, default_item_group, auto_create_missing, logger):
	"""Process a single WooCommerce variation dictionary into a Woo Item."""
	from frappe.utils import flt

	var_id = var.get("id")
	sku = (var.get("sku") or "").strip()
	permalink = var.get("permalink", "")
	description = html.unescape(var.get("description", "") or "")

	if not sku:
		sku = f"WC-{var_id}"

	# Find existing Woo Item
	existing_name = (
		frappe.db.get_value("Woo Item", {"woo_product_id": var_id})
		or frappe.db.get_value("Woo Item", {"sku": sku})
		or (frappe.db.exists("Woo Item", sku) and sku)
	)

	if existing_name:
		woo_item = frappe.get_doc("Woo Item", existing_name)
	else:
		woo_item = frappe.new_doc("Woo Item")
		woo_item.sku = sku

	woo_item.woo_product_id = var_id
	woo_item.woo_product_url = html.unescape(permalink) if permalink else ""
	woo_item.woo_description = description
	woo_item.published = 1
	woo_item.published_as = "template-variation"

	if "manage_stock" in var:
		woo_item.manage_stock = 1 if var.get("manage_stock") else 0

	reg_p = flt(var.get("regular_price") or 0)
	sale_p = flt(var.get("sale_price") or 0)
	if reg_p > 0:
		woo_item.regular_price = reg_p
	if sale_p > 0:
		woo_item.sale_price = sale_p

	# Set image if available
	img = var.get("image")
	if img and isinstance(img, dict) and img.get("src"):
		woo_item.image = img["src"]

	# Resolve parent template in ERPNext
	parent_woo_name = frappe.db.get_value("Woo Item", {"woo_product_id": parent_id})
	parent_item_code = frappe.db.get_value("Woo Item", parent_woo_name, "item_code") if parent_woo_name else None
	if parent_item_code:
		woo_item.variant_of = parent_item_code

	# Auto-link to ERPNext Item
	matched_item = (
		frappe.db.get_value("Item", {"item_code": sku}, "name")
		or frappe.db.get_value("Item", {"name": sku}, "name")
	)

	if matched_item:
		woo_item.item_code = matched_item
		woo_item.sync_status = "Synced"
	else:
		if auto_create_missing and not frappe.db.exists("Item", sku):
			try:
				new_item = frappe.new_doc("Item")
				new_item.item_code = sku
				new_item.item_name = sku
				new_item.item_group = default_item_group
				new_item.stock_uom = "Nos"
				new_item.is_stock_item = 1
				if parent_item_code:
					new_item.variant_of = parent_item_code
				if description:
					new_item.description = description
				new_item.insert(ignore_permissions=True)
				woo_item.item_code = new_item.name
				woo_item.sync_status = "Synced"
			except Exception as e:
				logger.error(f"Failed to auto-create variant Item {sku}: {e}")
				woo_item.sync_status = "Not Synced"
		elif not woo_item.item_code:
			woo_item.sync_status = "Not Synced"

	woo_item.flags.in_woo_sync = True
	woo_item.flags.ignore_version = True
	woo_item.save(ignore_permissions=True)
	return {"woo_item": woo_item.name, "sku": sku, "woo_product_id": var_id}


@frappe.whitelist()
def fetch_single_item_by_sku(sku, auto_create_missing=False):
	"""Fetch a single product (or variation) from WooCommerce by SKU.

	Args:
		sku (str): WooCommerce SKU to search and import
		auto_create_missing (bool): Whether to create ERPNext Item if not matched

	Returns:
		dict: Details of the fetched item (status, woo_item_name, item_code, etc.)
	"""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	if not sku or not str(sku).strip():
		frappe.throw(_("Please provide a valid SKU."))

	sku = str(sku).strip()
	auto_create_missing = _parse_bool(auto_create_missing)
	api = get_woo_api()
	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None) or "All Item Groups"
	logger = frappe.logger("woo_prime")

	# 1. Search WooCommerce top-level products by SKU
	prod = None
	try:
		resp = api.get("products", params={"sku": sku, "status": "any"})
		if resp.status_code == 200 and resp.json():
			prod = resp.json()[0]
	except Exception as e:
		logger.error(f"Error searching product by SKU '{sku}': {e}")

	# 2. If not found in top-level products, search if SKU belongs to a variation
	is_variation = False
	parent_id = None
	if not prod:
		variant_of = (
			frappe.db.get_value("Item", sku, "variant_of")
			or frappe.db.get_value("Item", {"item_code": sku}, "variant_of")
			or frappe.db.get_value("Woo Item", {"sku": sku}, "variant_of")
		)
		if variant_of:
			parent_woo_id = (
				frappe.db.get_value("Woo Item", {"item_code": variant_of}, "woo_product_id")
				or frappe.db.get_value("Woo Item", variant_of, "woo_product_id")
			)
			if parent_woo_id:
				try:
					v_resp = api.get(f"products/{parent_woo_id}/variations", params={"sku": sku})
					if v_resp.status_code == 200 and v_resp.json():
						prod = v_resp.json()[0]
						is_variation = True
						parent_id = parent_woo_id
				except Exception:
					pass

		# If still not found, search WooCommerce with general search query
		if not prod:
			try:
				search_resp = api.get("products", params={"search": sku, "status": "any"})
				if search_resp.status_code == 200 and search_resp.json():
					for candidate in search_resp.json():
						if (candidate.get("sku") or "").strip() == sku:
							prod = candidate
							break
						if candidate.get("type") == "variable" and candidate.get("id"):
							try:
								v_resp = api.get(f"products/{candidate['id']}/variations", params={"sku": sku})
								if v_resp.status_code == 200 and v_resp.json():
									prod = v_resp.json()[0]
									is_variation = True
									parent_id = candidate["id"]
									break
							except Exception:
								pass
			except Exception as e:
				logger.error(f"Error during general search for SKU '{sku}': {e}")

	if not prod:
		create_log(
			sync_type="Item",
			direction="Incoming",
			status="Failed",
			request_data={"sku": sku},
			error_message=f"Product with SKU '{sku}' not found on WooCommerce.",
		)
		frappe.throw(_("No product or variation found on WooCommerce with SKU: {0}").format(sku))

	# 3. Process the found product / variation
	variation_count = 0
	if is_variation:
		_process_single_variation(prod, parent_id, settings, default_item_group, auto_create_missing, logger)
	else:
		_process_single_product(prod, settings, default_item_group, auto_create_missing, logger)
		if prod.get("type") == "variable":
			try:
				var_resp = api.get(f"products/{prod['id']}/variations", params={"per_page": 100})
				if var_resp.status_code == 200 and var_resp.json():
					for v in var_resp.json():
						_process_single_variation(v, prod["id"], settings, default_item_group, auto_create_missing, logger)
						variation_count += 1
			except Exception as var_err:
				logger.warning(f"Failed to fetch variations for variable product {prod['id']}: {var_err}")

	frappe.db.commit()

	target_woo_id = prod.get("id")
	woo_item_name = (
		frappe.db.get_value("Woo Item", {"woo_product_id": target_woo_id})
		or frappe.db.get_value("Woo Item", {"sku": sku})
		or (frappe.db.exists("Woo Item", sku) and sku)
	)
	woo_item = frappe.get_doc("Woo Item", woo_item_name) if woo_item_name else None

	create_log(
		sync_type="Item",
		direction="Incoming",
		status="Success",
		reference_doctype="Woo Item",
		reference_name=woo_item.name if woo_item else None,
		woo_reference_id=str(target_woo_id),
		request_data={"sku": sku},
		response_data={"woo_product_id": target_woo_id, "sku": prod.get("sku"), "name": prod.get("name")},
	)

	extra_msg = f" (including {variation_count} variations)" if variation_count > 0 else ""
	return {
		"status": "success",
		"sku": sku,
		"woo_product_id": target_woo_id,
		"name": prod.get("name") or prod.get("sku") or sku,
		"woo_item_name": woo_item.name if woo_item else None,
		"item_code": woo_item.item_code if woo_item else None,
		"is_variation": is_variation,
		"variation_count": variation_count,
		"sync_status": woo_item.sync_status if woo_item else None,
		"message": _("Product <b>{0}</b> (SKU: {1}){2} fetched and synced successfully!").format(
			prod.get("name") or sku, sku, extra_msg
		),
	}


@frappe.whitelist()
def fetch_items_from_woocommerce(auto_create_missing=False, batch_size=10, skip_empty_products=False, background=True):
	"""Fetch products from WooCommerce in batches (default 10 items per request), create/update Woo Item records, and auto-link to ERPNext Items by SKU.
	
	If background=True, enqueues execution into Frappe background worker and returns immediately.
	
	Args:
		auto_create_missing (bool): Auto-create ERPNext Items for unmatched products (default False)
		batch_size (int): Products per API page request (default 10)
		skip_empty_products (bool): Skip products with empty name or SKU (default False)
		background (bool): If True, enqueue to background; if False, run synchronously (default True)
	
	Returns:
		dict: Status and message if background=True, or fetched/linked/created counts if background=False
	"""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	import json as _json
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	# Parse parameters with string support
	auto_create_missing = _parse_bool(auto_create_missing)
	skip_empty_products = _parse_bool(skip_empty_products)
	background = _parse_bool(background)
	batch_size = _parse_int(batch_size, default=10)

	if background:
		# Enqueue background job with job deduplication
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_item.woo_item.fetch_items_from_woocommerce_background",
			auto_create_missing=auto_create_missing,
			batch_size=batch_size,
			skip_empty_products=skip_empty_products,
			user=frappe.session.user,
			queue="long",
			timeout=7200,
			enqueue_after_commit=True,
			job_name=f"woo_fetch_products_{frappe.session.user}",
		)
		msg = _("Product fetch started in background ({0} items per page). You can monitor progress in real-time.").format(batch_size)
		if frappe.request:
			frappe.msgprint(msg, title=_("Fetch Queued"), indicator="blue")
		return {
			"status": "queued",
			"message": msg,
		}

	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None)
	if not default_item_group:
		default_item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"

	logger = frappe.logger("woo_prime", allow_site=True, max_size=10_000_000, file_count=50)
	api = get_woo_api()
	page = 1
	total_fetched = 0
	auto_linked = 0
	created_erpnext_items = 0
	skipped_empty = 0

	while True:
		req_params = {"per_page": batch_size, "page": page}
		logger.info(f"[FetchProducts] Requesting page {page} — GET products | params={req_params}")

		response = api.get("products", params=req_params)

		# Build request/response data for Woo Sync Log
		request_log = _json.dumps({
			"method": "GET",
			"endpoint": "products",
			"params": req_params,
			"url": response.url if hasattr(response, "url") else "",
		}, indent=2, default=str)

		# Truncate response body to 5000 chars to avoid DB bloat
		resp_body_preview = (response.text or "")[:5000]
		response_log = _json.dumps({
			"status_code": response.status_code,
			"headers": dict(response.headers) if hasattr(response, "headers") else {},
			"body_preview": resp_body_preview,
		}, indent=2, default=str)

		if response.status_code != 200:
			error_details = (response.text or "").strip()[:300]
			if not error_details:
				reason = getattr(response, "reason", "No details returned")
				error_details = f"HTTP Status {response.status_code} ({reason})"

			logger.error(f"[FetchProducts] FAILED page {page} — HTTP {response.status_code}: {error_details}")

			# Log failed request to Woo Sync Log
			create_log(
				sync_type="Item",
				direction="Incoming",
				status="Failed",
				request_data=request_log,
				response_data=response_log,
				error_message=f"HTTP {response.status_code}: {error_details}",
			)
			frappe.throw(_("Failed to fetch products from WooCommerce: {0}").format(error_details))

		products = response.json()

		logger.info(f"[FetchProducts] Page {page} — HTTP {response.status_code}, received {len(products)} products")

		if not products:
			break

		# Log successful batch fetch to Woo Sync Log
		create_log(
			sync_type="Item",
			direction="Incoming",
			status="Success",
			request_data=request_log,
			response_data=response_log,
		)

		for prod in products:
			woo_id = prod.get("id")
			sku = (prod.get("sku") or "").strip()
			name = html.unescape(prod.get("name") or "")
			permalink = prod.get("permalink", "")
			description = html.unescape(prod.get("description", "") or "")
			short_description = html.unescape(prod.get("short_description", "") or "")

			# Skip empty products if enabled
			if skip_empty_products and (not sku or not name):
				logger.info(f"[FetchProducts] Skipping empty product ID {woo_id} (sku={sku}, name={name})")
				skipped_empty += 1
				continue

			if not sku:
				sku = f"WC-{woo_id}"

			# Find existing Woo Item by woo_product_id, sku field, or primary key name
			existing_name = (
				frappe.db.get_value("Woo Item", {"woo_product_id": woo_id})
				or frappe.db.get_value("Woo Item", {"sku": sku})
				or (frappe.db.exists("Woo Item", sku) and sku)
			)

			if existing_name:
				woo_item = frappe.get_doc("Woo Item", existing_name)
			else:
				woo_item = frappe.new_doc("Woo Item")
				woo_item.sku = sku

			woo_item.woo_product_id = woo_id
			woo_item.woo_product_url = html.unescape(permalink) if permalink else ""
			woo_item.woo_description = description
			woo_item.woo_short_description = short_description
			woo_item.published = 1

			from frappe.utils import flt
			reg_p = flt(prod.get("regular_price") or 0)
			sale_p = flt(prod.get("sale_price") or 0)
			if reg_p > 0:
				woo_item.regular_price = reg_p
			if sale_p > 0:
				woo_item.sale_price = sale_p

			# Auto-link to ERPNext Item by matching SKU / item_code / name
			matched_item = (
				frappe.db.get_value("Item", {"item_code": sku}, "name")
				or frappe.db.get_value("Item", {"name": sku}, "name")
				or frappe.db.get_value("Item", {"item_name": name}, "name")
			)

			if matched_item:
				woo_item.item_code = matched_item
				woo_item.sync_status = "Synced"
				auto_linked += 1
			else:
				if auto_create_missing and not frappe.db.exists("Item", sku):
					try:
						new_item = frappe.new_doc("Item")
						new_item.item_code = sku
						new_item.item_name = name or sku
						new_item.item_group = default_item_group
						new_item.stock_uom = "Nos"
						new_item.is_stock_item = 1
						if description:
							new_item.description = description
						new_item.insert(ignore_permissions=True)

						woo_item.item_code = new_item.name
						woo_item.sync_status = "Synced"
						created_erpnext_items += 1
						auto_linked += 1
					except Exception as err:
						frappe.log_error(title="Auto-create Item Error", message=str(err))
						woo_item.sync_status = "Not Synced"
				elif not woo_item.item_code:
					woo_item.sync_status = "Not Synced"

			# Set flag to indicate this is an API sync operation
			woo_item.flags.in_woo_sync = True
			woo_item.save(ignore_permissions=True)
			total_fetched += 1

		page += 1

	frappe.db.commit()
	msg = _("✅ Fetched <b>{0}</b> products from WooCommerce!<br>🔗 Automatically linked <b>{1}</b> items to ERPNext Item Master.").format(
		total_fetched, auto_linked
	)
	if created_erpnext_items > 0:
		msg += _("<br>✨ Created <b>{0}</b> new ERPNext Item Master records.").format(created_erpnext_items)
	if skipped_empty > 0:
		msg += _("<br>⏭️ Skipped <b>{0}</b> empty products.").format(skipped_empty)

	frappe.msgprint(msg, title=_("Fetch Complete"), indicator="green")
	return {"fetched": total_fetched, "linked": auto_linked, "created": created_erpnext_items, "skipped": skipped_empty}


def fetch_items_from_woocommerce_background(
	auto_create_missing=False,
	batch_size=10,
	skip_empty_products=False,
	user=None
):
	"""Background worker function for fetching products from WooCommerce.
	
	This function is executed by the background worker process.
	It performs pagination, per-page commits, per-product savepoints,
	and real-time progress updates.
	
	Args:
		auto_create_missing (bool): Auto-create ERPNext Items for unmatched products (default False)
		batch_size (int): Products per API page request
		skip_empty_products (bool): Skip products with empty name or SKU
		user (str): User who initiated the fetch (for realtime notifications)
	
	Returns:
		dict: Statistics (fetched, linked, created, skipped, failed counts)
	"""
	import json as _json
	from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log

	# Check if within sync window (silently skip if outside)
	if not _is_within_sync_window():
		return {
			"status": "skipped",
			"message": "Outside auto sync window",
			"fetched": 0,
			"linked": 0,
			"created": 0,
			"skipped": 0,
			"failed": 0
		}

	# Initialize logger with site-specific logging
	logger = frappe.logger("woo_prime", allow_site=True, max_size=10_000_000, file_count=50)
	
	# Load Woo Settings and get default Item Group
	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None)
	if not default_item_group:
		default_item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	
	api = get_woo_api()
	
	# Initialize counters
	page = 1
	total_fetched = 0
	auto_linked = 0
	created_erpnext_items = 0
	skipped_empty = 0
	failed_products = 0
	
	# Send initial progress notification with status "starting"
	_publish_progress(
		user=user,
		operation="fetch_products",
		current_page=0,
		total_fetched=0,
		linked=0,
		created=0,
		skipped=0,
		failed=0,
		status="starting",
		message="Initializing product fetch..."
	)
	
	logger.info(f"[FetchProducts] Starting background fetch: auto_create_missing={auto_create_missing}, batch_size={batch_size}, skip_empty_products={skip_empty_products}")
	
	try:
		# Main pagination loop
		while True:
			req_params = {"per_page": batch_size, "page": page}
			logger.info(f"[FetchProducts] Page {page} — GET products | params={req_params}")
			
			# Send progress update
			_publish_progress(
				user=user,
				operation="fetch_products",
				current_page=page,
				total_fetched=total_fetched,
				linked=auto_linked,
				created=created_erpnext_items,
				skipped=skipped_empty,
				failed=failed_products,
				status="fetching",
				message=f"Fetching page {page}..."
			)
			
			# API request
			response = api.get("products", params=req_params)
			
			# Build request/response data for Woo Sync Log
			request_log = _json.dumps({
				"method": "GET",
				"endpoint": "products",
				"params": req_params,
				"page": page
			}, indent=2, default=str)
			
			resp_body_preview = (response.text or "")[:5000]
			response_log = _json.dumps({
				"status_code": response.status_code,
				"headers": dict(response.headers) if hasattr(response, "headers") else {},
				"body_preview": resp_body_preview
			}, indent=2, default=str)
			
			# Handle API errors
			if response.status_code != 200:
				error_details = (response.text or "").strip()[:300]
				if not error_details:
					reason = getattr(response, "reason", "No details returned")
					error_details = f"HTTP Status {response.status_code} ({reason})"
				
				logger.error(f"[FetchProducts] FAILED page {page} — HTTP {response.status_code}: {error_details}")
				
				# Log failed request
				create_log(
					sync_type="Item",
					direction="Incoming",
					status="Failed",
					request_data=request_log,
					response_data=response_log,
					error_message=f"HTTP {response.status_code}: {error_details}"
				)
				
				# Send error notification
				_publish_progress(
					user=user,
					operation="fetch_products",
					current_page=page,
					total_fetched=total_fetched,
					linked=auto_linked,
					created=created_erpnext_items,
					skipped=skipped_empty,
					failed=failed_products,
					status="error",
					message=f"API Error: {error_details}"
				)
				
				# Break pagination on API error
				break
			
			products = response.json()
			logger.info(f"[FetchProducts] Page {page} — received {len(products)} products")
			
			# Check for end of pagination
			if not products:
				break
			
			# Log successful batch fetch
			create_log(
				sync_type="Item",
				direction="Incoming",
				status="Success",
				request_data=request_log,
				response_data=response_log
			)
			
			# Process products with per-product savepoints
			page_linked = 0
			page_created = 0
			page_skipped = 0
			page_failed = 0
			
			for prod in products:
				woo_id = prod.get("id")
				sku = (prod.get("sku") or "").strip()
				name = html.unescape(prod.get("name") or "")
				
				# Skip empty products if enabled
				if skip_empty_products and (not sku or not name):
					logger.info(f"[FetchProducts] Skipping empty product ID {woo_id} (sku={sku}, name={name})")
					page_skipped += 1
					skipped_empty += 1
					continue
				
				# Process product with savepoint
				savepoint_name = f"product_{woo_id}"
				try:
					frappe.db.savepoint(savepoint_name)
					
					result = _process_single_product(
						prod=prod,
						settings=settings,
						default_item_group=default_item_group,
						auto_create_missing=auto_create_missing,
						logger=logger
					)
					
					# Update counters based on result
					if result["linked"]:
						page_linked += 1
						auto_linked += 1
					if result["created"]:
						page_created += 1
						created_erpnext_items += 1
					
					total_fetched += 1
					
				except Exception as prod_error:
					# Rollback to savepoint
					frappe.db.rollback(savepoint_name)
					
					# Log error with traceback
					error_traceback = frappe.get_traceback()
					logger.error(f"[FetchProducts] Product {woo_id} failed: {prod_error}\n{error_traceback}")
					
					# Create Individual_Error_Log
					create_log(
						sync_type="Item",
						direction="Incoming",
						status="Failed",
						woo_reference_id=str(woo_id),
						error_message=f"Product {woo_id} (SKU: {sku}): {str(prod_error)[:500]}"
					)
					
					page_failed += 1
					failed_products += 1
			
			# Commit page
			try:
				frappe.db.commit()
				logger.info(
					f"[FetchProducts] Page {page} committed: "
					f"{len(products)} products, {page_linked} linked, "
					f"{page_created} created, {page_skipped} skipped, {page_failed} failed"
				)
			except Exception as commit_error:
				logger.error(f"[FetchProducts] Commit failed for page {page}: {commit_error}")
				frappe.db.rollback()
				
				# Send error notification
				_publish_progress(
					user=user,
					operation="fetch_products",
					current_page=page,
					total_fetched=total_fetched,
					linked=auto_linked,
					created=created_erpnext_items,
					skipped=skipped_empty,
					failed=failed_products,
					status="error",
					message=f"Database commit failed: {str(commit_error)[:200]}"
				)
				break
			
			# Send progress update after page commit
			_publish_progress(
				user=user,
				operation="fetch_products",
				current_page=page,
				total_fetched=total_fetched,
				linked=auto_linked,
				created=created_erpnext_items,
				skipped=skipped_empty,
				failed=failed_products,
				status="in_progress",
				message=f"Page {page} complete ({len(products)} products processed)"
			)
			
			page += 1
		
		# Send final completion notification
		msg = _(
			"✅ Fetched <b>{0}</b> products from WooCommerce!<br>"
			"🔗 Automatically linked <b>{1}</b> items to ERPNext Item Master."
		).format(total_fetched, auto_linked)
		
		if created_erpnext_items > 0:
			msg += _("<br>✨ Created <b>{0}</b> new ERPNext Item Master records.").format(created_erpnext_items)
		
		if skipped_empty > 0:
			msg += _("<br>⏭️ Skipped <b>{0}</b> empty products.").format(skipped_empty)
		
		if failed_products > 0:
			msg += _("<br>⚠️ <b>{0}</b> products failed (see Woo Sync Log).").format(failed_products)
		
		_publish_progress(
			user=user,
			operation="fetch_products",
			current_page=page - 1,
			total_fetched=total_fetched,
			linked=auto_linked,
			created=created_erpnext_items,
			skipped=skipped_empty,
			failed=failed_products,
			status="completed",
			message=msg
		)
		
		logger.info(
			f"[FetchProducts] Completed: {total_fetched} fetched, {auto_linked} linked, "
			f"{created_erpnext_items} created, {skipped_empty} skipped, {failed_products} failed"
		)
		
		return {
			"status": "success",
			"fetched": total_fetched,
			"linked": auto_linked,
			"created": created_erpnext_items,
			"skipped": skipped_empty,
			"failed": failed_products
		}
		
	except Exception as e:
		# Fatal error
		error_traceback = frappe.get_traceback()
		logger.error(f"[FetchProducts] Fatal error: {e}\n{error_traceback}")
		
		# Send error notification
		_publish_progress(
			user=user,
			operation="fetch_products",
			current_page=page,
			total_fetched=total_fetched,
			linked=auto_linked,
			created=created_erpnext_items,
			skipped=skipped_empty,
			failed=failed_products,
			status="error",
			message=f"Fatal error: {str(e)[:200]}"
		)
		
		raise


@frappe.whitelist()
def auto_link_unlinked_items():
	"""Scan all Woo Items without item_code and automatically link them to ERPNext Items with matching SKU."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	unlinked = frappe.get_all("Woo Item", filters={"item_code": ["in", ["", None]]}, fields=["name", "sku"])
	linked_count = 0

	for row in unlinked:
		sku = row.sku
		if not sku:
			continue

		matched_item = (
			frappe.db.get_value("Item", {"item_code": sku}, "name")
			or frappe.db.get_value("Item", {"name": sku}, "name")
		)

		if matched_item:
			doc = frappe.get_doc("Woo Item", row.name)
			doc.item_code = matched_item
			doc.sync_status = "Synced"
			doc.save(ignore_permissions=True)
			linked_count += 1

	frappe.db.commit()
	frappe.msgprint(
		_("✅ Auto-linked {0} Woo Items to ERPNext Items by SKU!").format(linked_count),
		title=_("Auto Link Complete"),
		indicator="green",
	)
	return linked_count


@frappe.whitelist()
def bulk_link_to_erpnext_item(items, target_item_code=None):
	"""Bulk link selected Woo Items to an ERPNext Item or match automatically by SKU."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	import json

	if isinstance(items, str):
		items = json.loads(items)

	updated = 0
	for item_name in items:
		doc = frappe.get_doc("Woo Item", item_name)
		item_to_link = target_item_code

		if not item_to_link:
			# Auto match by SKU
			item_to_link = (
				frappe.db.get_value("Item", {"item_code": doc.sku}, "name")
				or frappe.db.get_value("Item", {"name": doc.sku}, "name")
			)

		if item_to_link and frappe.db.exists("Item", item_to_link):
			doc.item_code = item_to_link
			doc.sync_status = "Synced"
			doc.save(ignore_permissions=True)
			updated += 1

	frappe.db.commit()
	return updated


@frappe.whitelist()
def create_erpnext_items_from_woo(items):
	"""Bulk auto-create ERPNext Item records for Woo Items that don't have matching ERPNext items."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	import json

	if isinstance(items, str):
		items = json.loads(items)

	settings = frappe.get_single("Woo Settings")
	default_item_group = getattr(settings, "default_item_group", None)
	if not default_item_group:
		default_item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"

	created_count = 0

	for item_name in items:
		woo_item = frappe.get_doc("Woo Item", item_name)
		if woo_item.item_code and frappe.db.exists("Item", woo_item.item_code):
			continue

		target_code = (woo_item.sku or woo_item.name).strip()
		if not target_code:
			continue

		# Check if Item already exists in ERPNext
		if not frappe.db.exists("Item", target_code):
			new_item = frappe.new_doc("Item")
			new_item.item_code = target_code
			new_item.item_name = woo_item.name or target_code
			new_item.item_group = default_item_group
			new_item.stock_uom = "Nos"
			new_item.is_stock_item = 1
			if woo_item.woo_description:
				new_item.description = woo_item.woo_description
			new_item.insert(ignore_permissions=True)
			created_count += 1

		woo_item.item_code = target_code
		woo_item.sync_status = "Synced"
		woo_item.save(ignore_permissions=True)

	frappe.db.commit()
	frappe.msgprint(
		_("✅ Auto-created <b>{0}</b> ERPNext Item(s) and linked them to Woo Items!").format(created_count),
		title=_("Items Created"),
		indicator="green",
	)
	return created_count


@frappe.whitelist()
def publish_item_from_item_master(item_code, background=True, sync_images=True):
	"""Publish an ERPNext Item to WooCommerce directly from the Item Master form.

	Finds or auto-creates the linked Woo Item, then triggers publish_to_woocommerce.

	Args:
		item_code: ERPNext Item code
		background: If True, runs publish in background worker
		sync_images: Whether to sync product images (defaults to True)

	Returns:
		dict with status, woo_product_id, woo_product_url, woo_item_name
	"""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	if not item_code or not frappe.db.exists("Item", item_code):
		frappe.throw(_("Item {0} not found.").format(item_code))

	background = _parse_bool(background)
	if sync_images is None:
		sync_images = True
	else:
		sync_images = _parse_bool(sync_images)
	item = frappe.get_doc("Item", item_code)

	# Find existing Woo Item linked to this item_code
	woo_item_name = frappe.db.get_value("Woo Item", {"item_code": item_code})

	if not woo_item_name:
		# Also try matching by SKU = item_code
		woo_item_name = frappe.db.get_value("Woo Item", {"sku": item_code})

	if woo_item_name:
		woo_item = frappe.get_doc("Woo Item", woo_item_name)
		# Ensure item_code and image are linked
		changed = False
		if not woo_item.item_code:
			woo_item.item_code = item_code
			changed = True
		if item.image and not getattr(woo_item, "image", None):
			woo_item.image = item.image
			changed = True
		if changed:
			woo_item.save(ignore_permissions=True)
	else:
		# Auto-create a Woo Item for this ERPNext Item
		settings = frappe.get_single("Woo Settings")
		from woo_prime.api.sync import _get_item_price, _resolve_price_list
		from frappe.utils import flt

		price_list = _resolve_price_list(settings)
		price = _get_item_price(item_code, price_list)

		woo_item = frappe.new_doc("Woo Item")
		woo_item.sku = item_code
		woo_item.item_code = item_code
		woo_item.image = item.image
		woo_item.item_name = html.unescape(item.item_name or item_code)
		woo_item.regular_price = flt(price) if price else 0
		if item.description:
			woo_item.woo_description = item.description
		woo_item.save(ignore_permissions=True)
		frappe.db.commit()

	if background:
		frappe.enqueue(
			"woo_prime.woo_prime.doctype.woo_item.woo_item._publish_single_woo_item_worker",
			woo_item_name=woo_item.name,
			user=frappe.session.user,
			queue="long",
			timeout=1800,
			enqueue_after_commit=True,
			job_name=f"woo_publish_{woo_item.name}",
			sync_images=sync_images,
		)
		msg = _("Publishing '{0}' to WooCommerce in the background...").format(item.item_name or item_code)
		if frappe.request:
			frappe.msgprint(msg, title=_("Publish Queued"), indicator="blue")
		return {
			"status": "queued",
			"message": msg,
			"woo_item_name": woo_item.name,
		}
	else:
		return woo_item.publish_to_woocommerce(background=False, sync_images=sync_images)


@frappe.whitelist()
def fetch_price_from_erpnext(item_code=None, docname=None):
	"""Whitelisted helper callable by doc or item_code."""
	if docname and frappe.db.exists("Woo Item", docname):
		doc = frappe.get_doc("Woo Item", docname)
		return doc.fetch_price_from_erpnext()
	if item_code:
		return get_item_price_rate(item_code)
	return {"regular_price": 0.0, "sale_price": 0.0}


@frappe.whitelist()
def get_item_price_rate(item_code):
	"""Get current ERPNext item price rate and Pricing Rule sale price for an Item."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	if not item_code:
		return {"rate": 0.0, "regular_price": 0.0, "sale_price": 0.0}
	settings = frappe.get_single("Woo Settings")
	from woo_prime.api.sync import _get_item_price, _resolve_price_list
	price_list = _resolve_price_list(settings)
	regular_price = _get_item_price(item_code, price_list)

	sale_price = 0.0
	if getattr(settings, "enable_sale_price_sync", 1) and regular_price > 0:
		try:
			from woo_prime.api.sale_price_sync import _get_sale_price_for_item
			calc_sale, _, _ = _get_sale_price_for_item(item_code, regular_price, settings)
			if calc_sale is not None:
				sale_price = flt(calc_sale)
		except Exception as e:
			frappe.logger("woo_prime").debug(f"Sale price error in get_item_price_rate: {e}")

	return {
		"rate": regular_price,
		"regular_price": regular_price,
		"sale_price": sale_price,
	}


@frappe.whitelist()
def update_regular_prices(items=None):
	"""Bulk update regular_price from ERPNext Item Price for specified items or all Woo Items with empty price."""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
	import json
	if isinstance(items, str):
		items = json.loads(items)

	settings = frappe.get_single("Woo Settings")
	from woo_prime.api.sync import _get_item_price, _resolve_price_list
	from frappe.utils import flt
	price_list = _resolve_price_list(settings)

	if items:
		woo_items = frappe.get_all(
			"Woo Item",
			filters={"name": ["in", items], "item_code": ["!=", ""]},
			fields=["name", "item_code", "regular_price"]
		)
	else:
		woo_items = frappe.get_all(
			"Woo Item",
			filters={"item_code": ["!=", ""], "regular_price": ["in", [0, None]]},
			fields=["name", "item_code", "regular_price"]
		)

	updated_count = 0
	for row in woo_items:
		price = _get_item_price(row.item_code, price_list)
		if price and flt(price) > 0:
			doc = frappe.get_doc("Woo Item", row.name)
			doc.regular_price = flt(price)
			doc.save(ignore_permissions=True)
			updated_count += 1

	frappe.db.commit()
	return updated_count


def _delete_linked_sync_logs(reference_doctype, reference_name):
	"""Delete all Woo Sync Log entries that reference the given document.

	Frappe blocks deletion of a document that is referenced by Link / Dynamic Link
	fields in other DocTypes.  Woo Sync Log stores (reference_doctype, reference_name)
	pointing at Woo Item docs, so we must remove those log entries before the Woo
	Item document itself can be deleted.
	"""
	logs = frappe.get_all(
		"Woo Sync Log",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		},
		pluck="name",
	)
	for log_name in logs:
		frappe.delete_doc("Woo Sync Log", log_name, ignore_permissions=True, force=True)


@frappe.whitelist()
def delete_woo_item(woo_item_name, delete_item_master=False, delete_from_woocommerce=False):
	"""Delete a Woo Item with optional cascade to ERPNext Item Master and/or WooCommerce.

	Args:
		woo_item_name (str): Name of the Woo Item document.
		delete_item_master (bool|int): If truthy, also delete from ERPNext Item Master.
		delete_from_woocommerce (bool|int): If truthy, also delete the product from WooCommerce.

	Handles:
		- Simple product → delete the single WC product.
		- Template (has_variants) → delete WC variable product (all WC variations cascade).
		  Also delete all variant Woo Item docs and, if delete_item_master, all variant Items.
		- Variant (variant_of set) → delete only the WC variation under its parent product.
	"""
	frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])

	delete_item_master = _parse_bool(delete_item_master)
	delete_from_woocommerce = _parse_bool(delete_from_woocommerce)

	woo_item = frappe.get_doc("Woo Item", woo_item_name)
	item_code = woo_item.item_code
	woo_product_id = woo_item.woo_product_id
	item_name_label = woo_item.item_name or woo_item_name

	# Resolve ERPNext Item to determine template / variant status
	is_template = False
	is_variant = False
	variant_of = None
	if item_code:
		item_doc = frappe.get_doc("Item", item_code) if frappe.db.exists("Item", item_code) else None
		if item_doc:
			is_template = bool(item_doc.has_variants)
			variant_of = item_doc.variant_of
			is_variant = bool(variant_of)

	errors = []

	# --- WooCommerce deletion ---
	if delete_from_woocommerce and woo_product_id:
		from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api
		api = get_woo_api()
		try:
			if is_template or not is_variant:
				api.delete_product(woo_product_id)
			else:
				# Resolve the real parent from Woo instead of trusting the stored ID
				from woo_prime.api.sync import _resolve_parent_woo_id
				real = api.get_product(woo_product_id) or {}
				real_parent = real.get("parent_id") or _resolve_parent_woo_id(
					variant_of, woo_item.woo_product_type
				)
				if real_parent:
					api.delete_variation(real_parent, woo_product_id)
		except frappe.ValidationError as exc:
			# 404 only counts as "gone" if the product itself is really absent
			if api.get_product(woo_product_id):
				errors.append(f"WooCommerce: still exists after 404 ({exc})")
			else:
				frappe.log_error(title=f"WooCommerce already gone: {woo_item_name}", message=str(exc))
		except Exception as exc:
			errors.append(f"WooCommerce: {exc}")
			frappe.log_error(title=f"WooCommerce delete failed: {woo_item_name}", message=frappe.get_traceback())

	# --- ERPNext Item Master deletion ---
	if delete_item_master and item_code:
		try:
			if is_template:
				# Delete all variant Items first
				variant_items = frappe.get_all(
					"Item", filters={"variant_of": item_code}, pluck="name"
				)
				for v_item in variant_items:
					frappe.delete_doc("Item", v_item, ignore_permissions=True, force=True)
			# Delete the template/simple item itself
			frappe.delete_doc("Item", item_code, ignore_permissions=True, force=True)
		except Exception as exc:
			errors.append(f"ERPNext Item: {exc}")
			frappe.log_error(
				title=f"ERPNext Item delete failed: {woo_item_name}",
				message=frappe.get_traceback(),
			)

	# WooCommerce delete failed হলে Woo Item মুছবেন না
	if errors and delete_from_woocommerce:
		frappe.throw(_("Not deleted, WooCommerce error:\n{0}").format("\n".join(errors)))

	# --- Woo Item doc deletion (always) ---
	success_deleted_items = []
	try:
		if is_template:
			# Delete all variant Woo Items linked to variants of this template
			if item_code:
				variant_codes = frappe.get_all(
					"Item", filters={"variant_of": item_code}, pluck="name"
				)
				if variant_codes:
					variant_woo_items = frappe.get_all(
						"Woo Item",
						filters={
							"item_code": ["in", variant_codes],
						},
						fields=["name", "item_code", "sku", "woo_product_id"]
					)
					for v_wi in variant_woo_items:
						_delete_linked_sync_logs("Woo Item", v_wi.name)
						success_deleted_items.append(v_wi)
						frappe.delete_doc("Woo Item", v_wi.name, ignore_permissions=True, force=True)

		# Store info before deleting
		deleted_item_info = {
			"item_code": woo_item.item_code,
			"sku": woo_item.sku,
			"woo_product_id": woo_item.woo_product_id
		}

		_delete_linked_sync_logs("Woo Item", woo_item_name)
		frappe.delete_doc("Woo Item", woo_item_name, ignore_permissions=True, force=True)
		success_deleted_items.append(deleted_item_info)
	except Exception as exc:
		errors.append(f"Woo Item doc: {exc}")
		frappe.log_error(
			title=f"Woo Item doc delete failed: {woo_item_name}",
			message=frappe.get_traceback(),
		)

	# Log the delete event
	from woo_prime.woo_prime.doctype.woo_sync_log.woo_sync_log import create_log
	status = "Failed" if errors else "Success"
	error_msg = "\n".join(errors) if errors else ""
	parts = [f"Woo Item: {woo_item_name}"]
	if delete_from_woocommerce:
		parts.append("WooCommerce: yes")
	if delete_item_master:
		parts.append("ERPNext Item: yes")
	if is_template:
		parts.append("Type: Template (cascade)")
	elif is_variant:
		parts.append("Type: Variant")
	else:
		parts.append("Type: Simple")
	create_log(
		sync_type="Item",
		direction="Outgoing",
		status=status,
		woo_reference_id=str(woo_product_id or ""),
		request_data={
			"action": "delete",
			"woo_item_name": woo_item_name,
			"item_name": item_name_label,
			"delete_from_woocommerce": bool(delete_from_woocommerce),
			"delete_item_master": bool(delete_item_master),
			"details": parts,
		},
		response_data={"status": status, "errors": errors} if errors else {"status": "Success", "deleted": True},
		error_message=error_msg or None,
	)

	# Create tombstones for successfully deleted items
	if not errors and success_deleted_items:
		from woo_prime.woo_prime.doctype.woo_ignored_item.woo_ignored_item import create_tombstone
		for item_info in success_deleted_items:
			if isinstance(item_info, dict):
				create_tombstone(
					item_code=item_info.get("item_code"),
					sku=item_info.get("sku"),
					woo_product_id=item_info.get("woo_product_id"),
					reason="User deleted"
				)

	if errors:
		frappe.throw(
			_("Woo Item deleted but some errors occurred:\n{0}").format("\n".join(errors))
		)

	return {"status": "ok", "message": _("Deleted successfully: {0}").format(item_name_label)}


def _parse_item_codes(item_codes):
	"""Helper to parse item_codes passed as JSON string or list."""
	if isinstance(item_codes, str):
		try:
			return json.loads(item_codes)
		except Exception:
			return [c.strip() for c in item_codes.split(",") if c.strip()]
	elif isinstance(item_codes, (list, tuple)):
		return list(item_codes)
	return []



