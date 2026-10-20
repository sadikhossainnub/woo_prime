# Copyright (c) 2026, prime tech bd and Contributors
# See license.txt

import unittest
from datetime import datetime, time, timedelta

try:
	from frappe.tests.utils import FrappeTestCase
except ImportError:
	FrappeTestCase = unittest.TestCase

from woo_prime.woo_prime.doctype.woo_item.woo_item import _is_within_sync_window


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class TestSyncWindow(FrappeTestCase):
	"""Unit tests for _is_within_sync_window."""

	def test_normal_window_inside(self):
		# Window: 02:00 to 06:00
		start = "02:00:00"
		end = "06:00:00"
		now = time(3, 30)
		self.assertTrue(_is_within_sync_window(now_val=now, start_val=start, end_val=end))

	def test_normal_window_outside(self):
		start = "02:00:00"
		end = "06:00:00"
		self.assertFalse(_is_within_sync_window(now_val=time(1, 59), start_val=start, end_val=end))
		self.assertFalse(_is_within_sync_window(now_val=time(6, 1), start_val=start, end_val=end))
		self.assertFalse(_is_within_sync_window(now_val=time(12, 0), start_val=start, end_val=end))

	def test_normal_window_boundaries(self):
		start = "02:00:00"
		end = "06:00:00"
		self.assertTrue(_is_within_sync_window(now_val=time(2, 0), start_val=start, end_val=end))
		self.assertTrue(_is_within_sync_window(now_val=time(6, 0), start_val=start, end_val=end))

	def test_overnight_window_inside(self):
		# Window: 22:00 to 06:00
		start = "22:00:00"
		end = "06:00:00"
		self.assertTrue(_is_within_sync_window(now_val=time(23, 0), start_val=start, end_val=end))
		self.assertTrue(_is_within_sync_window(now_val=time(2, 0), start_val=start, end_val=end))
		self.assertTrue(_is_within_sync_window(now_val=time(5, 59), start_val=start, end_val=end))
		self.assertTrue(_is_within_sync_window(now_val=time(22, 0), start_val=start, end_val=end))
		self.assertTrue(_is_within_sync_window(now_val=time(6, 0), start_val=start, end_val=end))

	def test_overnight_window_outside(self):
		start = "22:00:00"
		end = "06:00:00"
		self.assertFalse(_is_within_sync_window(now_val=time(12, 0), start_val=start, end_val=end))
		self.assertFalse(_is_within_sync_window(now_val=time(21, 59), start_val=start, end_val=end))
		self.assertFalse(_is_within_sync_window(now_val=time(6, 1), start_val=start, end_val=end))

	def test_timedelta_inputs(self):
		start = timedelta(hours=8, minutes=30)
		end = timedelta(hours=17, minutes=0)
		self.assertTrue(_is_within_sync_window(now_val=timedelta(hours=10), start_val=start, end_val=end))
		self.assertFalse(_is_within_sync_window(now_val=timedelta(hours=7), start_val=start, end_val=end))
		self.assertFalse(_is_within_sync_window(now_val=timedelta(hours=18), start_val=start, end_val=end))

	def test_mixed_inputs(self):
		# Start as string, end as timedelta, now as time
		start = "21:00:00"
		end = timedelta(hours=5)
		self.assertTrue(_is_within_sync_window(now_val=time(22, 0), start_val=start, end_val=end))
		self.assertTrue(_is_within_sync_window(now_val="04:00:00", start_val=start, end_val=end))
		self.assertFalse(_is_within_sync_window(now_val=time(12, 0), start_val=start, end_val=end))
