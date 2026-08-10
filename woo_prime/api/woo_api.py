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
		self.base_url = f"{url.rstrip('/')}/wp-json/{self.API_VERSION}"
		self.auth = HTTPBasicAuth(consumer_key, consumer_secret)
		self.timeout = 30

	def _request(self, method, endpoint, data=None, params=None):
		"""Make an authenticated request to WooCommerce API."""
		url = f"{self.base_url}/{endpoint}"
		headers = {"Content-Type": "application/json"}

		response = requests.request(
			method=method,
			url=url,
			auth=self.auth,
			headers=headers,
			json=data,
			params=params,
			timeout=self.timeout,
		)

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
