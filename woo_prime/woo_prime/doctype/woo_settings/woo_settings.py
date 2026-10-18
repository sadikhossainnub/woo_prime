# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import os

import frappe
import requests
from frappe import _
from frappe.model.document import Document


class WooSettings(Document):
	def onload(self):
		self.webhook_delivery_url = frappe.utils.get_url("/api/method/woo_prime.api.webhook.handle_order")

	def validate(self):
		if self.woo_site_url:
			self.woo_site_url = self.woo_site_url.strip().rstrip("/")
			if self.woo_site_url.endswith("/index.php"):
				self.woo_site_url = self.woo_site_url[:-10].rstrip("/")

		if self.consumer_key:
			self.consumer_key = self.consumer_key.strip()

		if self.order_email_notification and not self.notification_email:
			frappe.throw(_("Notification Email Address is required when Email Notification is enabled."))


	@frappe.whitelist()
	@frappe.whitelist()
	def test_connection(self):
		"""Test WooCommerce API connection."""
		import json
		from urllib.parse import urlparse
		try:
			api = get_woo_api()
			host = urlparse(self.woo_site_url or "").netloc or "your-site.com"
			ua_used = getattr(api, "user_agent", "curl/8.5.0")

			# Attempt 1: system_status endpoint
			response = api.get("system_status")
			if response.status_code == 200:
				try:
					data = response.json()
					if isinstance(data, dict):
						environment = data.get("environment", {})
						wc_version = environment.get("version", "Unknown")
						wp_version = environment.get("wp_version", "Unknown")
						frappe.msgprint(
							f"✅ Connection Successful!<br>"
							f"WooCommerce Version: <b>{wc_version}</b><br>"
							f"WordPress Version: <b>{wp_version}</b>",
							title="Connection Test",
							indicator="green",
						)
						return
				except (ValueError, json.JSONDecodeError):
					pass

			# Attempt 2: Fallback test with products endpoint
			prod_response = api.get("products", params={"per_page": 1})
			if prod_response.status_code == 200:
				try:
					prod_data = prod_response.json()
					if isinstance(prod_data, list):
						frappe.msgprint(
							"✅ Connection Successful!<br>"
							"Successfully connected to WooCommerce REST API (Products endpoint).",
							title="Connection Test",
							indicator="green",
						)
						return
				except (ValueError, json.JSONDecodeError):
					pass

			active_resp = prod_response if ('prod_response' in locals() and prod_response is not None) else response
			resp_text = (active_resp.text or "").strip()
			server_hdr = active_resp.headers.get("Server") or active_resp.headers.get("server") or "N/A"
			cf_ray = active_resp.headers.get("cf-ray") or active_resp.headers.get("CF-RAY") or "N/A"
			is_cf = "cloudflare" in server_hdr.lower() or "attention required" in resp_text.lower() or "sorry, you have been blocked" in resp_text.lower() or active_resp.status_code == 403

			# If HTTP 200 returned HTML or non-JSON body
			if active_resp.status_code == 200:
				escaped_preview = frappe.utils.escape_html(resp_text[:300]) if resp_text else "Empty response body"
				msg = (
					"❌ Connection Failed!<br>"
					"The WooCommerce site returned <b>HTTP 200 OK</b>, but the response was not valid JSON.<br><br>"
					"<b>Troubleshooting Non-JSON / HTML Response:</b><br>"
					"1. <b>WordPress Permalinks:</b> Go to WP Admin → Settings → Permalinks and change structure from <i>'Plain'</i> to <i>'Post name'</i>.<br>"
					"2. <b>WooCommerce Plugin:</b> Verify WooCommerce plugin is installed and activated.<br>"
					"3. <b>Site URL:</b> Ensure <i>Woo Site URL</i> is correct and does not redirect to a login page.<br>"
					"4. <b>Security/Cache Plugins:</b> Disable security or caching rules returning HTML pages instead of REST API JSON.<br><br>"
					f"<b>Response Preview:</b> <code>{escaped_preview}</code>"
				)
				frappe.msgprint(msg, title="Connection Test", indicator="red")
				return

			msg = f"❌ Connection Failed!<br>Status Code: {active_resp.status_code}<br>"
			if is_cf:
				msg += (
					f"<br><b>Cloudflare Diagnostic Info:</b><br>"
					f"• <b>CF-Ray ID:</b> <code>{frappe.utils.escape_html(cf_ray)}</code><br>"
					f"• <b>Server Header:</b> <code>{frappe.utils.escape_html(server_hdr)}</code><br>"
					f"• <b>User-Agent Used:</b> <code>{frappe.utils.escape_html(ua_used)}</code><br><br>"
					"<b>Troubleshooting Cloudflare Block:</b><br>"
					f"1. <b>Search Ray ID in Cloudflare:</b> Go to Cloudflare → Security → Events and search Ray ID <code>{frappe.utils.escape_html(cf_ray)}</code> to see which rule blocked the request.<br>"
					"2. <b>Add Scoped Skip Rule:</b> In Cloudflare Dashboard → Security → WAF → Custom Rules, add a Skip rule scoped to your server IP:<br>"
					f"   <code>(http.host eq \"{frappe.utils.escape_html(host)}\" and starts_with(http.request.uri.path, \"/wp-json/\") and ip.src in {{&lt;server IPv4&gt; &lt;server IPv6 /64&gt;}})</code><br>"
					"   <i>Action:</i> <b>Skip</b> (WAF components, Bot Fight Mode, Browser Integrity Check).<br>"
					"   <i>Note:</i> The ERPNext server may connect over IPv6, so include both IPv4 and IPv6 /64 in the IP list.<br>"
					"3. <b>API Key Permissions:</b> Verify REST API Key has <b>Read/Write</b> permissions in WP Admin → WooCommerce → Settings → Advanced → REST API.<br><br>"
				)
			elif active_resp.status_code == 404:
				msg += (
					"<br><b>Troubleshooting 404 Not Found:</b><br>"
					"1. <b>WordPress Permalinks:</b> Go to WP Admin → Settings → Permalinks and change structure from <i>'Plain'</i> to <i>'Post name'</i>.<br>"
					"2. <b>WooCommerce Plugin:</b> Verify WooCommerce is installed and active.<br>"
					"3. <b>Site URL:</b> Ensure <i>Woo Site URL</i> is entered correctly.<br>"
					"4. <b>Apache Config:</b> Ensure <code>mod_rewrite</code> is enabled and <code>AllowOverride All</code> is set in Apache.<br><br>"
				)
			elif active_resp.status_code in (401, 403):
				msg += (
					"<br><b>Troubleshooting Auth Error (401 / 403):</b><br>"
					"1. <b>REST API Key Permissions:</b> Go to WP Admin → WooCommerce → Settings → Advanced → REST API. Edit your API Key and ensure permissions are set to <b>Read/Write</b>.<br>"
					"2. <b>User Account Permissions:</b> Ensure the WordPress user associated with the REST API Key has <b>Administrator</b> or <b>Shop Manager</b> role.<br>"
					"3. <b>Check Credentials:</b> Verify there are no trailing/leading spaces in Consumer Key or Consumer Secret.<br>"
					"4. <b>Apache Authorization Header:</b> If your web server strips HTTP Authorization headers, add the following to your WordPress <code>.htaccess</code> file:<br>"
					"<code>SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1</code> or <code>CGIPassAuth On</code><br><br>"
				)
			escaped_preview = frappe.utils.escape_html(resp_text[:500]) if resp_text else "Empty body"
			msg += f"Response Preview: <code>{escaped_preview}</code>"
			frappe.msgprint(
				msg,
				title="Connection Test",
				indicator="red",
			)
		except Exception as e:
			frappe.log_error(title="woo_prime: test_connection error", message=frappe.get_traceback())
			frappe.msgprint(
				f"❌ Connection Failed!<br>Error: {frappe.utils.escape_html(str(e))}",
				title="Connection Test",
				indicator="red",
			)

	@frappe.whitelist()
	def run_full_sync(self):
		"""Run full end-to-end sync."""
		return run_full_sync()

	@frappe.whitelist()
	def fetch_missing_order(self, woo_order_id=None):
		"""Fetch a specific order from WooCommerce by ID."""
		return fetch_missing_order(woo_order_id)

	@frappe.whitelist()
	def fetch_shipping_from_woo(self):
		"""Fetch WooCommerce shipping zones and methods."""
		return fetch_shipping_from_woo()


@frappe.whitelist()
def generate_api_shared_secret():
	"""Generate a random 40-character API shared secret."""
	return frappe.generate_hash(length=40)


@frappe.whitelist()
def download_wordpress_plugin():
	"""Download the Woo Prime Connector WordPress plugin zip file."""
	plugin_path = frappe.get_app_path("woo_prime", "wordpress_plugin", "woo-prime-connector.zip")

	if not os.path.exists(plugin_path):
		frappe.throw(_("WordPress plugin package file not found."))

	with open(plugin_path, "rb") as f:
		file_content = f.read()

	frappe.response["filename"] = "woo-prime-connector.zip"
	frappe.response["filecontent"] = file_content
	frappe.response["type"] = "download"


def get_woo_api():
	"""Get WooCommerce API client instance."""
	from woo_prime.api.woo_api import WooAPI

	settings = frappe.get_single("Woo Settings")
	if not settings.enabled:
		frappe.throw("WooCommerce integration is not enabled. Please enable it in Woo Settings.")

	user_agent = getattr(settings, "api_user_agent", None)

	return WooAPI(
		url=settings.woo_site_url,
		consumer_key=settings.consumer_key,
		consumer_secret=settings.get_password("consumer_secret"),
		user_agent=user_agent,
	)



@frappe.whitelist()
def run_full_sync():
	"""Run full end-to-end sync: Sync Categories -> Fetch Products & Auto Link -> Sync Stock & Price."""
	from woo_prime.woo_prime.doctype.woo_category.woo_category import sync_categories_from_woo
	from woo_prime.woo_prime.doctype.woo_item.woo_item import fetch_items_from_woocommerce
	from woo_prime.api.sync import sync_all_stock, sync_all_prices

	cat_count = sync_categories_from_woo()
	item_res = fetch_items_from_woocommerce()
	sync_all_stock()
	sync_all_prices()

	summary = _(
		"✅ <b>Full Sync Completed Successfully!</b><br>"
		"📂 Categories Synced: <b>{0}</b><br>"
		"📦 Products Fetched: <b>{1}</b><br>"
		"🔗 SKU Auto-linked: <b>{2}</b><br>"
		"📊 Stock & Prices Pushed to WooCommerce."
	).format(
		cat_count,
		item_res.get("fetched", 0) if isinstance(item_res, dict) else 0,
		item_res.get("linked", 0) if isinstance(item_res, dict) else 0,
	)

	frappe.msgprint(summary, title=_("Full Sync Complete"), indicator="green")
	return summary


@frappe.whitelist()
def fetch_missing_order(woo_order_id):
	"""Fetch a specific order from WooCommerce by ID and sync it as a Sales Order in ERPNext."""
	if not woo_order_id:
		frappe.throw(_("Please provide a WooCommerce Order ID."))

	woo_order_id = str(woo_order_id).strip()
	api = get_woo_api()

	response = api.get(f"orders/{woo_order_id}")
	if response.status_code != 200:
		frappe.throw(_("Failed to fetch order #{0} from WooCommerce: {1}").format(woo_order_id, response.text[:200]))

	order_data = response.json()
	from woo_prime.api.sync import sync_order

	sync_order(order_data)

	so_name = frappe.db.get_value("Sales Order", {"woo_order_id": woo_order_id})
	if so_name:
		frappe.msgprint(
			_("✅ WooCommerce Order #{0} successfully synced to Sales Order <b>{1}</b>!").format(
				woo_order_id, so_name
			),
			title=_("Order Synced"),
			indicator="green",
		)
		return so_name
	else:
		frappe.msgprint(
			_("⚠️ Processed order payload, but Sales Order was not created (check Woo Sync Log for details)."),
			indicator="orange",
		)
		return None


@frappe.whitelist()
def auto_map_attributes():
	"""Auto-match and populate ERPNext Item Attributes to WooCommerce Global Attributes in Woo Settings."""
	settings = frappe.get_single("Woo Settings")
	existing_erp_attrs = {row.erpnext_attribute for row in (settings.attribute_mappings or [])}

	# Get all Woo Attributes
	woo_attrs = frappe.get_all("Woo Attribute", fields=["name", "attribute_name", "woo_attribute_id", "slug"])
	if not woo_attrs:
		from woo_prime.woo_prime.doctype.woo_attribute.woo_attribute import fetch_attributes_from_woo
		fetch_attributes_from_woo()
		woo_attrs = frappe.get_all("Woo Attribute", fields=["name", "attribute_name", "woo_attribute_id", "slug"])

	woo_dict = {}
	for wa in woo_attrs:
		name_key = (wa.attribute_name or "").strip().lower()
		slug_key = (wa.slug or "").strip().lower().replace("pa_", "")
		woo_dict[name_key] = wa
		woo_dict[slug_key] = wa

	erp_attributes = frappe.get_all("Item Attribute", fields=["name", "attribute_name"])
	mapped_count = 0

	for ea in erp_attributes:
		attr_name = ea.name or ea.attribute_name
		if attr_name in existing_erp_attrs:
			continue

		lookup_key = attr_name.strip().lower()
		clean_key = frappe.scrub(attr_name).replace("_", "-").lower()

		matched_wa = woo_dict.get(lookup_key) or woo_dict.get(clean_key)
		if matched_wa:
			settings.append("attribute_mappings", {
				"erpnext_attribute": attr_name,
				"woo_attribute": matched_wa.name,
				"woo_attribute_id": matched_wa.woo_attribute_id,
				"woo_attribute_slug": matched_wa.slug,
			})
			frappe.db.set_value("Woo Attribute", matched_wa.name, "erpnext_item_attribute", attr_name)
			existing_erp_attrs.add(attr_name)
			mapped_count += 1

	if mapped_count > 0:
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
		frappe.db.commit()

	msg = _("Successfully auto-mapped {0} attribute(s)!").format(mapped_count)
	if frappe.request:
		frappe.msgprint(msg, title=_("Attribute Auto-Mapping"), indicator="green" if mapped_count else "blue")

	return {"mapped_count": mapped_count, "message": msg}


@frappe.whitelist()
def fetch_shipping_from_woo():
	"""Fetch WooCommerce shipping zones and methods, and map them to ERPNext Shipping Rules."""
	from frappe.utils import flt
	import re

	api = get_woo_api()
	settings = frappe.get_single("Woo Settings")

	# 1. Fetch shipping zones
	try:
		resp = api.get("shipping/zones")
		if resp.status_code != 200:
			frappe.throw(_("Failed to fetch shipping zones from WooCommerce (Status {0}): {1}").format(
				resp.status_code, resp.text[:200]
			))
		zones = resp.json() if isinstance(resp.json(), list) else []
	except Exception as e:
		frappe.log_error(title="woo_prime: fetch_shipping_from_woo error", message=frappe.get_traceback())
		frappe.throw(_("Error fetching shipping zones from WooCommerce: {0}").format(str(e)))

	# Include Zone 0: Rest of the World (Locations not covered by other zones)
	zones.append({"id": 0, "name": "Locations not covered by other zones"})

	fetched_methods = []
	for zone in zones:
		zone_id = zone.get("id")
		zone_name = zone.get("name") or f"Zone {zone_id}"

		try:
			m_resp = api.get(f"shipping/zones/{zone_id}/methods")
			if m_resp.status_code != 200:
				continue
			methods = m_resp.json()
			if not isinstance(methods, list):
				continue

			for m in methods:
				if not m.get("enabled", True):
					continue

				settings_obj = m.get("settings", {})
				m_title = m.get("title")
				if not m_title and isinstance(settings_obj, dict):
					m_title = settings_obj.get("title", {}).get("value")
				if not m_title:
					m_title = m.get("method_title") or f"{zone_name} Delivery"
				m_title = str(m_title).strip()

				cost = 0.0
				if isinstance(settings_obj, dict):
					cost_val = settings_obj.get("cost", {}).get("value", 0)
					try:
						cost = flt(cost_val)
					except (ValueError, TypeError):
						try:
							match = re.search(r"[-+]?\d*\.?\d+", str(cost_val))
							if match:
								cost = flt(match.group(0))
						except Exception:
							cost = 0.0

				fetched_methods.append({
					"zone_id": zone_id,
					"zone_name": zone_name,
					"instance_id": m.get("id") or m.get("instance_id"),
					"method_id": m.get("method_id"),
					"title": m_title,
					"cost": cost,
				})
		except Exception as e:
			frappe.log_error(title=f"woo_prime: Error fetching methods for zone {zone_id}", message=frappe.get_traceback())

	# Fallback if no zones or zone methods found: try legacy shipping_methods endpoint
	if not fetched_methods:
		try:
			leg_resp = api.get("shipping_methods")
			if leg_resp.status_code == 200:
				leg_methods = leg_resp.json()
				if isinstance(leg_methods, list):
					for lm in leg_methods:
						fetched_methods.append({
							"zone_id": 0,
							"zone_name": "General",
							"instance_id": lm.get("id"),
							"method_id": lm.get("id"),
							"title": lm.get("title") or lm.get("name") or "Standard Shipping",
							"cost": 0.0,
						})
		except Exception:
			pass

	if not fetched_methods:
		msg = _("No shipping methods found on your WooCommerce store.")
		if frappe.request:
			frappe.msgprint(msg, title=_("WooCommerce Shipping Sync"), indicator="orange")
		return {"new_count": 0, "updated_count": 0, "total_found": 0, "message": msg}

	# Existing mappings in Woo Settings
	existing_mappings = {}
	for row in (settings.shipping_rule_mappings or []):
		if row.woo_shipping_method:
			existing_mappings[row.woo_shipping_method.strip().lower()] = row

	new_count = 0
	updated_count = 0
	company = settings.default_company

	for item in fetched_methods:
		title = item["title"]
		cost = item["cost"]
		lookup_key = title.strip().lower()

		# Disambiguate if already used by another zone
		if lookup_key in existing_mappings and item["zone_id"] != 0 and item["zone_name"] not in title:
			title = f"{item['zone_name']} - {title}"
			lookup_key = title.strip().lower()

		# Find or auto-create ERPNext Shipping Rule
		matched_rule = None
		if frappe.db.exists("Shipping Rule", title):
			matched_rule = title
		else:
			matched_rule = frappe.db.get_value("Shipping Rule", {"label": title}, "name")
			if not matched_rule and item["zone_name"] and frappe.db.exists("Shipping Rule", item["zone_name"]):
				matched_rule = item["zone_name"]

		# Auto-create Shipping Rule if it does not exist
		if not matched_rule:
			try:
				sr = frappe.new_doc("Shipping Rule")
				sr.shipping_rule_type = "Selling"
				sr.label = title
				if company:
					sr.company = company
				account = getattr(settings, "shipping_charge_account", None)
				if not account and company:
					account = frappe.db.get_value(
						"Account",
						{"company": company, "account_name": ["like", "%Shipping%"], "is_group": 0},
						"name",
					)
				if account:
					sr.account = account
				sr.calculate_based_on = "Fixed"
				sr.shipping_amount = cost
				sr.append("conditions", {
					"shipping_amount": cost,
				})
				sr.flags.ignore_permissions = True
				sr.flags.ignore_mandatory = True
				sr.insert(ignore_permissions=True)
				matched_rule = sr.name
			except Exception as e:
				frappe.log_error(title="woo_prime: Auto-create Shipping Rule failed", message=frappe.get_traceback())

		# Fallback to default_shipping_rule
		if not matched_rule and getattr(settings, "default_shipping_rule", None):
			matched_rule = settings.default_shipping_rule

		if lookup_key in existing_mappings:
			row = existing_mappings[lookup_key]
			changed = False
			if cost and flt(row.cost) != flt(cost):
				row.cost = cost
				changed = True
			if not row.shipping_rule and matched_rule:
				row.shipping_rule = matched_rule
				changed = True
			if changed:
				updated_count += 1
		else:
			settings.append("shipping_rule_mappings", {
				"woo_shipping_method": title,
				"shipping_rule": matched_rule,
				"cost": cost,
			})
			existing_mappings[lookup_key] = True
			new_count += 1

	if new_count > 0 or updated_count > 0:
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
		frappe.db.commit()

	msg = _(
		"✅ <b>WooCommerce Shipping Sync Completed!</b><br>"
		"📦 Total Methods Found: <b>{0}</b><br>"
		"➕ Added to Mappings: <b>{1}</b><br>"
		"🔄 Updated: <b>{2}</b>"
	).format(len(fetched_methods), new_count, updated_count)

	if frappe.request:
		frappe.msgprint(msg, title=_("Shipping Sync"), indicator="green" if (new_count or updated_count) else "blue")

	return {
		"new_count": new_count,
		"updated_count": updated_count,
		"total_found": len(fetched_methods),
		"message": msg,
	}


