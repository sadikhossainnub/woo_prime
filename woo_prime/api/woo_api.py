# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import json

import requests
from requests.auth import HTTPBasicAuth

import frappe
from frappe import _


DEFAULT_USER_AGENT = "curl/8.5.0"


def is_valid_json(response):
	"""Check if requests.Response contains valid JSON content."""
	if not response or not response.text or not response.text.strip():
		return False
	try:
		response.json()
		return True
	except Exception:
		return False


def is_cloudflare_response(response):
	"""Check if the response is a Cloudflare block or challenge page."""
	if not response:
		return False
	server_header = (response.headers.get("Server") or "").lower()
	text = (response.text or "").lower()
	if "attention required" in text or "sorry, you have been blocked" in text:
		return True
	if "cloudflare" in server_header and (response.status_code in (403, 503) or not is_valid_json(response)):
		return True
	return False


def is_woocommerce_json_error(response):
	"""Check if response is a valid WooCommerce / WordPress REST API JSON error payload."""
	if not is_valid_json(response):
		return False
	try:
		data = response.json()
		if isinstance(data, dict) and ("code" in data or "message" in data):
			return True
	except Exception:
		pass
	return False


class WooAPI:
	"""WooCommerce REST API v3 wrapper."""

	API_VERSION = "wc/v3"

	def __init__(self, url, consumer_key, consumer_secret, user_agent=None):
		site_url = url.rstrip("/")
		if site_url.endswith("/index.php"):
			site_url = site_url[:-10].rstrip("/")
		self.site_url = site_url
		consumer_key = consumer_key.strip() if consumer_key else ""
		consumer_secret = consumer_secret.strip() if consumer_secret else ""
		self.consumer_key = consumer_key
		self.consumer_secret = consumer_secret
		self.user_agent = user_agent.strip() if user_agent and user_agent.strip() else DEFAULT_USER_AGENT
		self.base_url = f"{self.site_url}/wp-json/{self.API_VERSION}"
		self.auth = HTTPBasicAuth(consumer_key, consumer_secret)
		self.timeout = 60

		# Try to load cached routing flags for this site
		_cache_key = f"woo_api_route_{self.site_url}"
		_cached = frappe.cache().get_value(_cache_key) or {}
		self.use_rest_route = _cached.get("use_rest_route", False)
		self.use_query_auth = _cached.get("use_query_auth", False)

		self.session = requests.Session()

	def _persist_route_flags(self):
		"""Persist current use_query_auth / use_rest_route to site cache."""
		try:
			_cache_key = f"woo_api_route_{self.site_url}"
			frappe.cache().set_value(_cache_key, {
				"use_rest_route": self.use_rest_route,
				"use_query_auth": self.use_query_auth,
			}, expires_in_sec=3600)
		except Exception:
			pass

	def _request(self, method, endpoint, data=None, params=None, timeout=None):
		"""Make an authenticated request to WooCommerce API."""
		headers = {
			"User-Agent": self.user_agent,
			"Accept": "application/json, text/plain, */*",
		}
		if data is not None:
			headers["Content-Type"] = "application/json"

		logger = frappe.logger("woo_prime", allow_site=True, max_size=1, file_count=50)
		req_timeout = timeout or self.timeout

		def make_call(use_rest, use_query):
			req_params = dict(params) if params else {}
			if use_query:
				# Require https for query-auth (credentials in URL params)
				if not self.site_url.startswith("https://"):
					frappe.throw(
						_("Query-auth fallback requires HTTPS. Site URL '{0}' is not HTTPS.").format(self.site_url),
						title=_("WooCommerce Security Error"),
					)
				req_params["consumer_key"] = self.consumer_key
				req_params["consumer_secret"] = self.consumer_secret
				auth = None
			else:
				auth = self.auth

			if use_rest:
				url = self.site_url
				req_params["rest_route"] = f"/{self.API_VERSION}/{endpoint}"
			else:
				url = f"{self.base_url}/{endpoint}"

			# Log the outgoing request
			safe_params = {k: v for k, v in req_params.items() if k not in ("consumer_key", "consumer_secret")}
			logger.info(f"[WooAPI] {method} → {url} | params={safe_params} | rest_route={use_rest} | query_auth={use_query} | timeout={req_timeout}")

			resp = self.session.request(
				method=method,
				url=url,
				auth=auth,
				headers=headers,
				json=data,
				params=req_params,
				timeout=req_timeout,
			)

			# Log the response
			logger.info(f"[WooAPI] ← HTTP {resp.status_code} from {url} | body_preview={resp.text[:300]}")

			return resp

		def make_call_with_retry(use_rest, use_query):
			"""Retry on transient errors: 429, 502, 503, 504 with exponential backoff."""
			import time
			delays = [1, 2, 4]
			last_resp = None
			for attempt, delay in enumerate(delays + [None]):
				try:
					last_resp = make_call(use_rest, use_query)
					if last_resp.status_code not in (429, 502, 503, 504):
						return last_resp
					if delay is None:
						break
					logger.warning(
						f"[WooAPI] HTTP {last_resp.status_code} on attempt {attempt + 1}, "
						f"retrying in {delay}s…"
					)
					time.sleep(delay)
				except requests.exceptions.RequestException:
					if delay is None:
						raise
					time.sleep(delay)
			return last_resp

		# Initial request with exception handling
		try:
			response = make_call_with_retry(self.use_rest_route, self.use_query_auth)
		except requests.exceptions.RequestException as req_err:
			logger.error(f"[WooAPI] Connection error to {self.site_url}: {req_err}")
			frappe.throw(
				_("Unable to connect to WooCommerce site at '{0}'. Error: {1}").format(self.site_url, str(req_err)),
				title=_("WooCommerce Connection Error"),
			)

		# Fallback 1: If HTTP Basic Auth fails with 401/403, check if query param auth should be attempted.
		# Only run query auth fallback if response is a genuine WooCommerce/WordPress JSON error and NOT a Cloudflare block.
		if response.status_code in (401, 403) and not self.use_query_auth:
			if is_cloudflare_response(response):
				return response
			if is_woocommerce_json_error(response):
				try:
					fallback_response = make_call_with_retry(self.use_rest_route, True)
					if fallback_response.status_code not in (401, 403):
						self.use_query_auth = True
						self._persist_route_flags()
						return fallback_response
					elif fallback_response.status_code == 404 and not self.use_rest_route:
						fallback_response2 = make_call_with_retry(True, True)
						if fallback_response2.status_code not in (401, 403, 404):
							self.use_query_auth = True
							self.use_rest_route = True
							self._persist_route_flags()
							return fallback_response2
				except Exception:
					frappe.log_error(title="woo_prime: Query Auth Fallback Error", message=frappe.get_traceback())

		# Fallback 2: If direct wp-json endpoint returned 404 or HTTP 200 with non-JSON HTML (e.g. Plain Permalinks), try rest_route fallback
		if not self.use_rest_route and (response.status_code == 404 or (response.status_code == 200 and not is_valid_json(response))):
			if is_cloudflare_response(response):
				return response
			try:
				fallback_response = make_call_with_retry(True, self.use_query_auth)
				if fallback_response.status_code in (401, 403) and not self.use_query_auth:
					if is_woocommerce_json_error(fallback_response):
						fallback_response2 = make_call_with_retry(True, True)
						if fallback_response2.status_code not in (401, 403, 404) and is_valid_json(fallback_response2):
							self.use_query_auth = True
							self.use_rest_route = True
							self._persist_route_flags()
							return fallback_response2
				elif fallback_response.status_code == 200 and is_valid_json(fallback_response):
					self.use_rest_route = True
					self._persist_route_flags()
					return fallback_response
				elif fallback_response.status_code != 404 and is_valid_json(fallback_response):
					self.use_rest_route = True
					self._persist_route_flags()
					return fallback_response
			except Exception:
				frappe.log_error(title="woo_prime: REST Route Fallback Error", message=frappe.get_traceback())

		return response


	def get(self, endpoint, params=None, timeout=None):
		"""GET request."""
		return self._request("GET", endpoint, params=params, timeout=timeout)

	def post(self, endpoint, data=None, timeout=None):
		"""POST request."""
		return self._request("POST", endpoint, data=data, timeout=timeout)

	def put(self, endpoint, data=None, timeout=None):
		"""PUT request."""
		return self._request("PUT", endpoint, data=data, timeout=timeout)

	def delete(self, endpoint, params=None, timeout=None):
		"""DELETE request."""
		return self._request("DELETE", endpoint, params=params, timeout=timeout)


	# --- Product Methods ---

	@staticmethod
	def _existing_woo_id(err_json):
		"""Parse resource_id from a product_invalid_sku error body.

		Returns int or None.  Callers use this to skip a create and go straight
		to an update on the already-existing WooCommerce product/variation.
		Never reads `unique_sku` – we never change the SKU.
		"""
		if not isinstance(err_json, dict):
			return None
		if err_json.get("code") != "product_invalid_sku":
			return None
		return err_json.get("data", {}).get("resource_id") or None

	@staticmethod
	def _is_image_error(response):
		"""Return True when a 400 response is caused by a WooCommerce image-fetch error.

		WooCommerce returns codes like 'woocommerce_product_image_upload_error' or
		'woocommerce_variation_image_upload_error' when it cannot fetch the image URL
		(e.g. internal host:port URL not reachable from the WooCommerce server).
		"""
		if response.status_code != 400:
			return False
		try:
			return "image" in (response.json().get("code") or "")
		except Exception:
			return False

	@staticmethod
	def _strip_images(data):
		"""Return a shallow copy of *data* without 'image' or 'images' keys."""
		return {k: v for k, v in data.items() if k not in ("image", "images")}

	def create_product(self, product_data):
		"""Create a product on WooCommerce.

		Decision order (no retry loops)
		──────────────────────────────────
		1. Pre-flight: if no woo_product_id saved but SKU exists in WooCommerce,
		   go straight to update (caller already does this via _publish_simple_item_to_woo
		   SKU lookup; this guard covers direct calls).
		2. POST → 200/201 → done.
		3. POST → 400 product_invalid_sku
		   a. Extract resource_id via _existing_woo_id() (from error body).
		   b. Fallback: GET products?sku= to find the ID if error body omits it.
		   c. _recover_and_update_product() – one update attempt, never loops back
		      into create_product.
		4. Any other status → frappe.throw.
		"""
		response = self.post("products", data=product_data)
		if response.status_code in (200, 201):
			return response.json()

		if response.status_code == 400:
			try:
				err_json = response.json()
				resource_id = self._existing_woo_id(err_json)
				if not resource_id:
					sku = product_data.get("sku")
					if sku:
						sr = self.get("products", params={"sku": sku, "status": "any"})
						if sr.status_code == 200 and sr.json():
							resource_id = sr.json()[0].get("id")
				if resource_id:
					return self._recover_and_update_product(resource_id, product_data)
			except Exception:
				frappe.log_error(title="woo_prime: Auto-link product failed", message=frappe.get_traceback())

		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def _recover_and_update_product(self, resource_id, product_data):
		"""Link to an existing WooCommerce product found by SKU (on duplicate-SKU failure).

		Only links the woo_product_id — does NOT overwrite name, description, or force status.
		Single update attempt per call – never calls create_product (no loop risk).
		If WooCommerce rejects due to image URL, retries once without images.
		"""
		# Only send the linking payload — preserve existing product name/description/status
		payload = {
			"sku": product_data.get("sku"),
		}
		# Carry over fields that are safe to update without overwriting existing data
		for safe_key in ("regular_price", "sale_price", "stock_quantity", "manage_stock",
		                 "stock_status", "images", "categories", "tags", "attributes",
		                 "meta_data", "external_url"):
			if safe_key in product_data:
				payload[safe_key] = product_data[safe_key]

		put_resp = self.put(f"products/{resource_id}", data=payload)
		if put_resp.status_code == 200:
			return put_resp.json()

		# Image URL unreachable from WooCommerce server → retry without images
		if self._is_image_error(put_resp):
			retry = self.put(f"products/{resource_id}", data=self._strip_images(payload))
			if retry.status_code == 200:
				frappe.log_error(
					title="woo_prime: Product linked to existing Woo product (image skipped)",
					message=(
						f"Linked to existing Woo product #{resource_id} "
						f"(SKU '{product_data.get('sku')}') and updated without image. "
						f"Image error: {put_resp.json().get('message', '')}"
					),
				)
				return retry.json()

		# resource_id might actually be a variation (parent_id > 0 in Woo response)
		try:
			info = self.get(f"products/{resource_id}")
			if info.status_code == 200:
				parent_id = info.json().get("parent_id")
				if parent_id:
					vr = self.put(f"products/{parent_id}/variations/{resource_id}", data=payload)
					if vr.status_code == 200:
						return vr.json()
					if self._is_image_error(vr):
						vr2 = self.put(
							f"products/{parent_id}/variations/{resource_id}",
							data=self._strip_images(payload),
						)
						if vr2.status_code == 200:
							return vr2.json()
		except Exception:
			pass

		frappe.throw(
			f"Failed to auto-update WooCommerce product #{resource_id} "
			f"(SKU '{product_data.get('sku')}'): "
			f"HTTP {put_resp.status_code} – {put_resp.text[:300]}"
		)

	def update_product(self, product_id, product_data, _retry_create=True):
		"""Update an existing product on WooCommerce.

		Args:
			product_id:    WooCommerce product ID
			product_data:  dict of fields to update
			_retry_create: when True, falls back to create if WooCommerce returns 404
		"""
		response = self.put(f"products/{product_id}", data=product_data)
		if response.status_code == 200:
			return response.json()
		if response.status_code == 404 and _retry_create:
			return self.create_product(product_data)
		if self._is_image_error(response):
			# Image URL unreachable – retry without images so other fields still sync
			retry = self.put(f"products/{product_id}", data=self._strip_images(product_data))
			if retry.status_code == 200:
				frappe.log_error(
					title="woo_prime: Product updated without image",
					message=f"Product #{product_id} updated without image: {response.json().get('message', '')}",
				)
				return retry.json()
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def create_product_variation(self, parent_id, variation_data):
		"""Create a variation under *parent_id* on WooCommerce.

		Decision order (no retry loops)
		──────────────────────────────────
		1. POST → 200/201 → done.
		2. POST → 400 product_invalid_sku
		   a. _existing_woo_id() extracts resource_id from error body.
		   b. Fallback: GET products/{parent_id}/variations?sku=.
		   c. _recover_and_update_variation() – one attempt, never loops.
		3. Other 400 / other status → frappe.throw.
		"""
		response = self.post(f"products/{parent_id}/variations", data=variation_data)
		if response.status_code in (200, 201):
			return response.json()

		if response.status_code == 400:
			try:
				err_json = response.json()
				resource_id = self._existing_woo_id(err_json)
				if not resource_id:
					sku = variation_data.get("sku")
					if sku:
						sr = self.get(f"products/{parent_id}/variations", params={"sku": sku})
						if sr.status_code == 200 and sr.json():
							resource_id = sr.json()[0].get("id")
				if resource_id:
					return self._recover_and_update_variation(parent_id, resource_id, variation_data)
			except Exception:
				frappe.log_error(title="woo_prime: Auto-link variation failed", message=frappe.get_traceback())

		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def _recover_and_update_variation(self, parent_id, resource_id, variation_data):
		"""Update an existing WooCommerce variation after a duplicate-SKU create failure.

		Try order (single attempt per path – no nested create calls):
		  1. PUT products/{parent_id}/variations/{resource_id}
		  2. 400 image error on (1) → same URL, strip 'image' key, retry once
		  3. GET products/{resource_id} to learn the real parent_id in WooCommerce;
		     retry with correct parent, handle image error there too
		  4. parent_id == 0 in WooCommerce → it is a simple product → PUT products/{resource_id}
		"""
		payload = dict(variation_data)
		payload["status"] = "publish"
		sku = variation_data.get("sku", "")

		def _put_var(p_id, r_id, data):
			return self.put(f"products/{p_id}/variations/{r_id}", data=data)

		# 1. Canonical path
		put_resp = _put_var(parent_id, resource_id, payload)
		if put_resp.status_code == 200:
			return put_resp.json()

		# 2. Image fetch error – retry without image key
		if self._is_image_error(put_resp):
			retry = _put_var(parent_id, resource_id, self._strip_images(payload))
			if retry.status_code == 200:
				frappe.log_error(
					title="woo_prime: Variation linked to existing Woo variation (image skipped)",
					message=(
						f"Linked variation #{resource_id} (parent {parent_id}, SKU '{sku}') "
						f"and updated without image. "
						f"WooCommerce error: {put_resp.json().get('message', '')}"
					),
				)
				return retry.json()

		# 3. Inspect the actual parent stored in WooCommerce
		res_info = None
		try:
			info = self.get(f"products/{resource_id}")
			if info.status_code == 200:
				res_info = info.json()
		except Exception:
			pass

		if res_info:
			actual_parent = res_info.get("parent_id") or 0

			if actual_parent and actual_parent != parent_id:
				# Variation lives under a different parent in WooCommerce
				alt = _put_var(actual_parent, resource_id, payload)
				if alt.status_code == 200:
					return alt.json()
				if self._is_image_error(alt):
					alt2 = _put_var(actual_parent, resource_id, self._strip_images(payload))
					if alt2.status_code == 200:
						frappe.log_error(
							title="woo_prime: Variation linked via alt parent (image skipped)",
							message=f"Variation #{resource_id} (SKU '{sku}') updated under parent {actual_parent} without image.",
						)
						return alt2.json()

			if not actual_parent:
				# Was stored as a simple product in WooCommerce
				sp = self.put(f"products/{resource_id}", data=payload)
				if sp.status_code == 200:
					return sp.json()
				if self._is_image_error(sp):
					sp2 = self.put(f"products/{resource_id}", data=self._strip_images(payload))
					if sp2.status_code == 200:
						frappe.log_error(
							title="woo_prime: Simple-product auto-link (image skipped)",
							message=f"Product #{resource_id} (SKU '{sku}') updated without image.",
						)
						return sp2.json()

		frappe.throw(
			f"Failed to auto-update WooCommerce variation #{resource_id} "
			f"(SKU '{sku}'): "
			f"HTTP {put_resp.status_code} – {put_resp.text[:300]}"
		)

	def update_product_variation(self, parent_id, variation_id, variation_data, _retry_create=True):
		"""Update a product variation on WooCommerce.

		If the variation no longer exists (404), falls back to create (once).
		If WooCommerce rejects the image URL, retries once without the image.
		"""
		response = self.put(f"products/{parent_id}/variations/{variation_id}", data=variation_data)
		if response.status_code == 200:
			return response.json()
		if response.status_code == 404 and _retry_create:
			return self.create_product_variation(parent_id, variation_data)
		if self._is_image_error(response):
			retry = self.put(
				f"products/{parent_id}/variations/{variation_id}",
				data=self._strip_images(variation_data),
			)
			if retry.status_code == 200:
				frappe.log_error(
					title="woo_prime: Variation updated without image",
					message=f"Variation #{variation_id} updated without image: {response.json().get('message', '')}",
				)
				return retry.json()
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def batch_products(self, batch_data):
		"""Batch create/update/delete products on WooCommerce.

		Args:
			batch_data: dict with "create", "update", and/or "delete" lists of product dicts

		Returns:
			dict: WooCommerce batch response
		"""
		response = self.post("products/batch", data=batch_data)
		if response.status_code in (200, 201):
			return response.json()
		else:
			frappe.throw(
				f"WooCommerce API Batch Products Error ({response.status_code}): {response.text[:500]}"
			)

	def batch_product_variations(self, parent_id, batch_data):
		"""Batch create/update product variations on WooCommerce.

		Args:
			parent_id: Parent WooCommerce product ID
			batch_data: dict with "create" list and/or "update" list of variation dicts

		Returns:
			dict: WooCommerce batch response containing "create" and "update" lists of results
		"""
		response = self.post(f"products/{parent_id}/variations/batch", data=batch_data)
		if response.status_code in (200, 201):
			return response.json()
		else:
			frappe.throw(
				f"WooCommerce API Batch Variations Error ({response.status_code}): {response.text[:500]}"
			)

	def upload_media(self, file_path, filename=None):
		"""Directly upload an image file binary to WordPress Media REST API (/wp-json/wp/v2/media).

		Args:
			file_path: Local file path or Frappe file URL string (e.g. /files/image.jpg)
			filename: Optional filename override

		Returns:
			dict: Uploaded WordPress media object containing 'id' and 'source_url'
		"""
		import os
		import mimetypes
		import re

		if not file_path or file_path.startswith("http://") or file_path.startswith("https://"):
			return None

		clean_url = file_path.lstrip("/")
		is_private = clean_url.startswith("private/files/")

		if is_private:
			# Check allow_private_images setting before proceeding
			try:
				settings = frappe.get_single("Woo Settings")
				if not getattr(settings, "allow_private_images", 0):
					frappe.logger("woo_prime").warning(
						f"upload_media: skipping private file {file_path} (allow_private_images disabled)"
					)
					return None
			except Exception:
				return None
			abs_path = frappe.get_site_path(clean_url)
		elif clean_url.startswith("files/"):
			abs_path = frappe.get_site_path("public", clean_url)
		else:
			abs_path = frappe.get_site_path("public", "files", clean_url)

		# Security: resolve symlinks and ensure path stays within allowed dirs
		try:
			abs_path = os.path.realpath(abs_path)
			site_path = os.path.realpath(frappe.get_site_path())
			public_files = os.path.join(site_path, "public", "files")
			private_files = os.path.join(site_path, "private", "files")
			if not (abs_path.startswith(public_files) or abs_path.startswith(private_files)):
				frappe.logger("woo_prime").warning(
					f"upload_media: path escape attempt blocked: {abs_path}"
				)
				return None
		except Exception:
			return None

		if not os.path.exists(abs_path):
			return None

		if not filename:
			raw_name = os.path.basename(abs_path)
			# Sanitize filename: allow only safe characters
			filename = re.sub(r"[^\w.\-]", "_", raw_name)
			filename = filename.lstrip(".")

		mime_type, _ = mimetypes.guess_type(abs_path)
		if not mime_type:
			mime_type = "image/jpeg"

		headers = {
			"Content-Type": mime_type,
			"Content-Disposition": f'attachment; filename="{filename}"',
			"User-Agent": self.user_agent,
			"Accept": "application/json, text/plain, */*",
		}


		try:
			with open(abs_path, "rb") as f:
				file_data = f.read()

			url = f"{self.site_url}/wp-json/wp/v2/media"
			req_params = {}
			auth = self.auth

			if self.use_query_auth:
				req_params["consumer_key"] = self.consumer_key
				req_params["consumer_secret"] = self.consumer_secret
				auth = None

			response = self.session.post(
				url=url,
				auth=auth,
				headers=headers,
				data=file_data,
				params=req_params if req_params else None,
				timeout=self.timeout,
			)

			if response.status_code in (200, 201):
				return response.json()

			elif response.status_code == 404:
				fallback_params = dict(req_params)
				fallback_params["rest_route"] = "/wp/v2/media"
				fallback_resp = self.session.post(
					url=self.site_url,
					auth=auth,
					headers=headers,
					data=file_data,
					params=fallback_params,
					timeout=self.timeout,
				)
				if fallback_resp.status_code in (200, 201):
					return fallback_resp.json()
				else:
					frappe.log_error(
						title="WordPress Media Upload Failed (rest_route fallback)",
						message=f"Status {fallback_resp.status_code}: {fallback_resp.text[:500]}"
					)
			else:
				frappe.log_error(
					title="WordPress Media Upload Failed",
					message=f"Status {response.status_code} for {filename}: {response.text[:500]}"
				)
		except Exception as e:
			frappe.log_error(
				title="WordPress Media Upload Exception",
				message=f"Exception uploading {file_path}: {e}"
			)

		return None

	def get_product(self, product_id):
		"""Get a product from WooCommerce."""
		response = self.get(f"products/{product_id}")
		if response.status_code == 200:
			return response.json()
		return None

	def update_stock(self, product_id, stock_quantity, manage_stock=True):
		"""Update stock quantity for a product.

		Args:
			product_id: WooCommerce product ID
			stock_quantity: New stock quantity
			manage_stock: Whether to enable stock management

		Returns:
			dict: WooCommerce product response

		Raises:
			frappe.ValidationError: on 404 (product not found in WooCommerce)
		"""
		data = {
			"manage_stock": manage_stock,
			"stock_quantity": int(stock_quantity),
			"stock_status": "instock" if int(stock_quantity) > 0 else "outofstock",
		}
		response = self.put(f"products/{product_id}", data=data)
		if response.status_code == 200:
			return response.json()
		if response.status_code == 404:
			frappe.throw(
				f"WooCommerce product #{product_id} not found (404) — mark as unpublished",
				frappe.ValidationError,
			)
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def update_variation_stock(self, parent_id, variation_id, stock_quantity, manage_stock=True):
		"""Update stock quantity for a product variation.

		Args:
			parent_id: Parent WooCommerce product ID
			variation_id: WooCommerce variation ID
			stock_quantity: New stock quantity
			manage_stock: Whether to enable stock management

		Returns:
			dict: WooCommerce variation response

		Raises:
			frappe.ValidationError: on 404 (variation not found in WooCommerce)
		"""
		data = {
			"manage_stock": manage_stock,
			"stock_quantity": int(stock_quantity),
			"stock_status": "instock" if int(stock_quantity) > 0 else "outofstock",
		}
		response = self.put(f"products/{parent_id}/variations/{variation_id}", data=data)
		if response.status_code == 200:
			return response.json()
		if response.status_code == 404:
			frappe.throw(
				f"WooCommerce variation #{variation_id} (parent #{parent_id}) not found (404) — mark as unpublished",
				frappe.ValidationError,
			)
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def update_price(self, product_id, regular_price, sale_price=None, clear_sale=False):
		"""Update price for a product.

		Args:
			product_id: WooCommerce product ID
			regular_price: Regular price
			sale_price: Optional sale price
			clear_sale: When True, explicitly send sale_price="" to clear any existing sale price

		Returns:
			dict: WooCommerce product response

		Raises:
			frappe.ValidationError: on 404 (product not found in WooCommerce)
		"""
		data = {"regular_price": str(regular_price)}
		if sale_price is not None:
			data["sale_price"] = str(sale_price)
		elif clear_sale:
			data["sale_price"] = ""
		response = self.put(f"products/{product_id}", data=data)
		if response.status_code == 200:
			return response.json()
		if response.status_code == 404:
			frappe.throw(
				f"WooCommerce product #{product_id} not found (404) — mark as unpublished",
				frappe.ValidationError,
			)
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def update_variation_price(self, parent_id, variation_id, regular_price, sale_price=None, clear_sale=False):
		"""Update price for a product variation.

		Args:
			parent_id: Parent WooCommerce product ID
			variation_id: WooCommerce variation ID
			regular_price: Regular price
			sale_price: Optional sale price
			clear_sale: When True, explicitly send sale_price="" to clear any existing sale price

		Returns:
			dict: WooCommerce variation response

		Raises:
			frappe.ValidationError: on 404 (variation not found in WooCommerce)
		"""
		data = {"regular_price": str(regular_price)}
		if sale_price is not None:
			data["sale_price"] = str(sale_price)
		elif clear_sale:
			data["sale_price"] = ""
		response = self.put(f"products/{parent_id}/variations/{variation_id}", data=data)
		if response.status_code == 200:
			return response.json()
		if response.status_code == 404:
			frappe.throw(
				f"WooCommerce variation #{variation_id} (parent #{parent_id}) not found (404) — mark as unpublished",
				frappe.ValidationError,
			)
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	# --- Order Methods ---


	def get_order(self, order_id):
		"""Get an order from WooCommerce."""
		response = self.get(f"orders/{order_id}")
		if response.status_code == 200:
			return response.json()
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	def update_order_status(self, order_id, status):
		"""Update order status on WooCommerce.

		Args:
			order_id: WooCommerce order ID
			status: New status (pending, processing, on-hold, completed, cancelled, refunded, failed)

		Returns:
			dict: WooCommerce order response
		"""
		response = self.put(f"orders/{order_id}", data={"status": status})
		if response.status_code == 200:
			return response.json()
		else:
			frappe.throw(
				f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
			)

	def get_orders(self, params=None):
		"""Get multiple orders from WooCommerce."""
		response = self.get("orders", params=params)
		if response.status_code == 200:
			return response.json()
		frappe.throw(f"WooCommerce API Error ({response.status_code}): {response.text[:500]}")

	# --- Customer Methods ---

	def get_customers(self, params=None):
		"""Get customers from WooCommerce."""
		response = self.get("customers", params=params)
		if response.status_code == 200:
			return response.json()
		return []
