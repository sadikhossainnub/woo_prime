# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import re
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class WooVariantGroup(Document):
	def validate(self):
		if not self.group_sku:
			frappe.throw(_("Group SKU is required."))

		self.group_sku = self.group_sku.strip()

		# Check SKU collision with Woo Item
		existing_sku_item = frappe.db.get_value("Woo Item", {"sku": self.group_sku}, ["name", "item_code"], as_dict=True)
		if existing_sku_item:
			frappe.throw(
				_("Group SKU '{0}' cannot be the same as Woo Item SKU (Item Code: {1}). "
				  "WooCommerce requires SKUs to be globally unique across products and variations.").format(
					self.group_sku, existing_sku_item.item_code
				)
			)

		# Auto-derive item_name from leader if empty
		if not self.item_name and self.leader:
			leader_name = frappe.db.get_value("Woo Item", self.leader, "item_name")
			if not leader_name:
				leader_item_code = frappe.db.get_value("Woo Item", self.leader, "item_code")
				if leader_item_code:
					leader_name = frappe.db.get_value("Item", leader_item_code, "item_name")
			if leader_name:
				clean_name = re.sub(r"-\d+-[A-Za-z0-9]+$", "", leader_name)
				if clean_name == leader_name:
					clean_name = re.sub(r"-\d+$", "", leader_name)
				self.item_name = clean_name.strip()

		# Verify leader belongs to this group if set
		if self.leader:
			leader_group = frappe.db.get_value("Woo Item", self.leader, "woo_variant_group")
			leader_mode = frappe.db.get_value("Woo Item", self.leader, "variant_publish_mode")
			if leader_group and leader_group != self.name:
				frappe.throw(_("Leader '{0}' already belongs to another group '{1}'.").format(self.leader, leader_group))

	def get_members(self):
		"""Return all member Woo Items linked to this group."""
		return frappe.get_all(
			"Woo Item",
			filters={"woo_variant_group": self.name},
			fields=[
				"name",
				"sku",
				"item_code",
				"item_name",
				"woo_product_id",
				"published",
				"manage_stock",
				"last_pushed_qty",
				"variant_publish_mode",
			],
			order_by="creation asc",
		)

	@frappe.whitelist()
	def get_group_members_info(self):
		"""Whitelisted API: Return rich member details including attributes and stock qty."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		members = self.get_members()
		settings = frappe.get_single("Woo Settings")
		warehouse = getattr(settings, "default_warehouse", None)
		from woo_prime.api.sync import _get_stock_qty

		res = []
		for m in members:
			stock_qty = _get_stock_qty(m.item_code, warehouse) if warehouse and m.item_code else 0
			attrs = {}
			if m.item_code:
				variant_attrs = frappe.get_all(
					"Item Variant Attribute",
					filters={"parent": m.item_code},
					fields=["attribute", "attribute_value"],
				)
				for a in variant_attrs:
					if a.attribute:
						attrs[a.attribute] = a.attribute_value or ""

			res.append({
				"woo_item_name": m.name,
				"sku": m.sku,
				"item_code": m.item_code,
				"item_name": m.item_name,
				"woo_product_id": m.woo_product_id,
				"published": bool(m.published),
				"is_leader": bool(m.name == self.leader),
				"stock_qty": int(stock_qty),
				"attributes": attrs,
			})
		return res

	@frappe.whitelist()
	def publish_group(self, sync_images=None):
		"""Whitelisted API: Publish this variant group variable product and its variations to WooCommerce."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		from woo_prime.api.sync import _publish_variant_group_product, _publish_variant_group_member
		from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

		api = get_woo_api()
		settings = frappe.get_single("Woo Settings")

		# Publish parent variable product
		prod_res = _publish_variant_group_product(self, api, settings, sync_images=sync_images)

		# Publish leader and all member variations
		if self.leader:
			leader_doc = frappe.get_doc("Woo Item", self.leader)
			leader_item = frappe.get_doc("Item", leader_doc.item_code)
			_publish_variant_group_member(leader_doc, leader_item, api, settings, sync_images=sync_images)

		self.reload()
		msg = _("✅ Group product '{0}' published to WooCommerce successfully! ID: <b>{1}</b>").format(
			self.item_name or self.group_sku, self.woo_product_id
		)
		if frappe.request:
			frappe.msgprint(msg, title=_("Group Published"), indicator="green")
		return {"status": "ok", "woo_product_id": self.woo_product_id, "woo_product_url": self.woo_product_url}

	@frappe.whitelist()
	def unpublish_group(self):
		"""Whitelisted API: Delete variable product from WooCommerce and set members to Not Published."""
		frappe.only_for(["System Manager", "Woo Manager", "Website Manager"])
		from woo_prime.woo_prime.doctype.woo_settings.woo_settings import get_woo_api

		if self.woo_product_id:
			api = get_woo_api()
			try:
				api.delete_product(self.woo_product_id, force=True)
			except Exception as e:
				err_str = str(e).lower()
				if "404" not in err_str and "not found" not in err_str:
					frappe.throw(_("Failed to delete WooCommerce product {0}: {1}").format(self.woo_product_id, e))

		# Reset all members
		members = self.get_members()
		for m in members:
			doc = frappe.get_doc("Woo Item", m.name)
			doc.woo_product_id = 0
			doc.published = 0
			doc.sync_status = "Not Synced"
			doc.published_as = ""
			doc.woo_variant_group = None
			doc.variant_publish_mode = "Not Published"
			doc.save(ignore_permissions=True)

		self.woo_product_id = 0
		self.published = 0
		self.woo_product_url = ""
		self.save(ignore_permissions=True)

		msg = _("Group '{0}' unpublished and members reset to Not Published.").format(self.name)
		if frappe.request:
			frappe.msgprint(msg, title=_("Group Unpublished"), indicator="orange")
		return {"status": "ok", "message": msg}
