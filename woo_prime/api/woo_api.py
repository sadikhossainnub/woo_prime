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
		self.consumer_key = consumer_key
		self.consumer_secret = consumer_secret
		self.base_url = f"{self.site_url}/wp-json/{self.API_VERSION}"
		self.auth = HTTPBasicAuth(consumer_key, consumer_secret)
		self.timeout = 30
		self.use_rest_route = False
		self.use_query_auth = False

	def _request(self, method, endpoint, data=None, params=None):
		"""Make an authenticated request to WooCommerce API."""
		headers = {"Content-Type": "application/json"}

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
		else:
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
		else:
			frappe.throw(
				f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
			)

	def create_product_variation(self, parent_id, variation_data):
		"""Create a product variation on WooCommerce."""
		response = self.post(f"products/{parent_id}/variations", data=variation_data)
		if response.status_code in (200, 201):
			return response.json()
		else:
			frappe.throw(
				f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
			)

	def update_product_variation(self, parent_id, variation_id, variation_data):
		"""Update a product variation on WooCommerce."""
		response = self.put(f"products/{parent_id}/variations/{variation_id}", data=variation_data)
		if response.status_code == 200:
			return response.json()
		else:
			frappe.throw(
				f"WooCommerce API Error ({response.status_code}): {response.text[:500]}"
			)

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
