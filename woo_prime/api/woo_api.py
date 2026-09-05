# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import json

import requests
from requests.auth import HTTPBasicAuth

import frappe


class WooAPI:
	"""WooCommerce REST API v3 wrapper."""

	API_VERSION = "wc/v3"

	def __init__(self, url, consumer_key, consumer_secret):
		site_url = url.rstrip("/")
		if site_url.endswith("/index.php"):
			site_url = site_url[:-10].rstrip("/")
		self.site_url = site_url
		consumer_key = consumer_key.strip() if consumer_key else ""
		consumer_secret = consumer_secret.strip() if consumer_secret else ""
		self.consumer_key = consumer_key
		self.consumer_secret = consumer_secret
		self.base_url = f"{self.site_url}/wp-json/{self.API_VERSION}"
		self.auth = HTTPBasicAuth(consumer_key, consumer_secret)
		self.timeout = 30
		self.use_rest_route = False
		self.use_query_auth = False

	def _request(self, method, endpoint, data=None, params=None):
		"""Make an authenticated request to WooCommerce API."""
		headers = {
			"Content-Type": "application/json",
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 WooPrime/1.0",
			"Accept": "application/json, text/plain, */*",
		}

		def make_call(use_rest, use_query):
			req_params = dict(params) if params else {}
			if use_query:
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

			return requests.request(
				method=method,
				url=url,
				auth=auth,
				headers=headers,
				json=data,
				params=req_params,
				timeout=self.timeout,
			)

		# Initial request
		response = make_call(self.use_rest_route, self.use_query_auth)

		# Fallback 1: If HTTP Basic Auth fails with 401/403 (e.g. Apache strips Authorization header), try Query Param Auth
		if response.status_code in (401, 403) and not self.use_query_auth:
			try:
				fallback_response = make_call(self.use_rest_route, True)
				if fallback_response.status_code not in (401, 403):
					self.use_query_auth = True
					return fallback_response
				elif fallback_response.status_code == 404 and not self.use_rest_route:
					fallback_response2 = make_call(True, True)
					if fallback_response2.status_code not in (401, 403, 404):
						self.use_query_auth = True
						self.use_rest_route = True
						return fallback_response2
			except Exception:
				pass

		# Fallback 2: If direct wp-json endpoint returned 404, try rest_route fallback
		if response.status_code == 404 and not self.use_rest_route:
			try:
				fallback_response = make_call(True, self.use_query_auth)
				if fallback_response.status_code in (401, 403) and not self.use_query_auth:
					fallback_response2 = make_call(True, True)
					if fallback_response2.status_code not in (401, 403, 404):
						self.use_query_auth = True
						self.use_rest_route = True
						return fallback_response2
				elif fallback_response.status_code != 404:
					self.use_rest_route = True
					return fallback_response
			except Exception:
				pass

		return response

	def get(self, endpoint, params=None):
		"""GET request."""
		return self._request("GET", endpoint, params=params)

	def post(self, endpoint, data=None):
		"""POST request."""
		return self._request("POST", endpoint, data=data)

	def put(self, endpoint, data=None):
		"""PUT request."""
		return self._request("PUT", endpoint, data=data)

	def delete(self, endpoint, params=None):
		"""DELETE request."""
		return self._request("DELETE", endpoint, params=params)

	# --- Product Methods ---

	def create_product(self, product_data):
		"""Create a product on WooCommerce.

		Args:
			product_data: dict with keys like name, sku, regular_price, description, etc.

		Returns:
			dict: WooCommerce product response
		"""
		response = self.post("products", data=product_data)
		if response.status_code in (200, 201):
			return response.json()
		elif response.status_code == 400:
			try:
				err_json = response.json()
				if err_json.get("code") == "product_invalid_sku":
					resource_id = err_json.get("data", {}).get("resource_id")
					if not resource_id and product_data.get("sku"):
						search_resp = self.get("products", params={"sku": product_data.get("sku")})
						if search_resp.status_code == 200 and search_resp.json():
							resource_id = search_resp.json()[0].get("id")
					if resource_id:
						return self.update_product(resource_id, product_data)
			except Exception:
				pass

		frappe.throw(
			f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
		)

	def update_product(self, product_id, product_data):
		"""Update an existing product on WooCommerce.

		Args:
			product_id: WooCommerce product ID
			product_data: dict with fields to update

		Returns:
			dict: WooCommerce product response
		"""
		response = self.put(f"products/{product_id}", data=product_data)
		if response.status_code == 200:
			return response.json()
		elif response.status_code == 404:
			# Product ID is no longer valid on WooCommerce, fallback to create
			return self.create_product(product_data)
		else:
			frappe.throw(
				f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
			)

	def create_product_variation(self, parent_id, variation_data):
		"""Create a product variation on WooCommerce."""
		response = self.post(f"products/{parent_id}/variations", data=variation_data)
		if response.status_code in (200, 201):
			return response.json()
		elif response.status_code == 400:
			try:
				err_json = response.json()
				if err_json.get("code") == "product_invalid_sku":
					resource_id = err_json.get("data", {}).get("resource_id")
					if not resource_id and variation_data.get("sku"):
						search_resp = self.get(f"products/{parent_id}/variations", params={"sku": variation_data.get("sku")})
						if search_resp.status_code == 200 and search_resp.json():
							resource_id = search_resp.json()[0].get("id")
					if resource_id:
						return self.update_product_variation(parent_id, resource_id, variation_data)
			except Exception:
				pass

		frappe.throw(
			f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
		)

	def update_product_variation(self, parent_id, variation_id, variation_data):
		"""Update a product variation on WooCommerce."""
		response = self.put(f"products/{parent_id}/variations/{variation_id}", data=variation_data)
		if response.status_code == 200:
			return response.json()
		elif response.status_code == 404:
			# Variation ID is no longer valid on WooCommerce under parent_id, fallback to create
			return self.create_product_variation(parent_id, variation_data)
		else:
			frappe.throw(
				f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
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

		if not file_path or file_path.startswith("http://") or file_path.startswith("https://"):
			return None

		clean_url = file_path.lstrip("/")
		if clean_url.startswith("files/"):
			abs_path = frappe.get_site_path("public", clean_url)
		elif clean_url.startswith("private/files/"):
			abs_path = frappe.get_site_path(clean_url)
		else:
			abs_path = frappe.get_site_path("public", "files", clean_url)

		if not os.path.exists(abs_path):
			return None

		if not filename:
			filename = os.path.basename(abs_path)

		mime_type, _ = mimetypes.guess_type(abs_path)
		if not mime_type:
			mime_type = "image/jpeg"

		headers = {
			"Content-Type": mime_type,
			"Content-Disposition": f'attachment; filename="{filename}"',
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 WooPrime/1.0",
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

			response = requests.post(
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
				fallback_resp = requests.post(
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
		"""
		data = {
			"manage_stock": manage_stock,
			"stock_quantity": int(stock_quantity),
		}
		return self.update_product(product_id, data)

	def update_price(self, product_id, regular_price, sale_price=None):
		"""Update price for a product.

		Args:
			product_id: WooCommerce product ID
			regular_price: Regular price
			sale_price: Optional sale price

		Returns:
			dict: WooCommerce product response
		"""
		data = {"regular_price": str(regular_price)}
		if sale_price is not None:
			data["sale_price"] = str(sale_price)
		return self.update_product(product_id, data)

	# --- Order Methods ---

	def get_order(self, order_id):
		"""Get an order from WooCommerce."""
		response = self.get(f"orders/{order_id}")
		if response.status_code == 200:
			return response.json()
		return None

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
		return []

	# --- Customer Methods ---

	def get_customers(self, params=None):
		"""Get customers from WooCommerce."""
		response = self.get("customers", params=params)
		if response.status_code == 200:
			return response.json()
		return []
