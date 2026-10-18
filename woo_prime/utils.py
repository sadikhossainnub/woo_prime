# Copyright (c) 2026, prime tech bd and contributors
# For license information, please see license.txt

import html
import re
import frappe


def repair_crashed_tables():
	"""Auto-repair MariaDB tables that frequently get corrupted on crashes (e.g. tabError Log)."""
	try:
		frappe.db.sql("REPAIR TABLE `tabError Log`")
		frappe.db.commit()
	except Exception:
		pass


def safe_log_error(title, message=None):
	"""Log error without crashing if tabError Log table is marked as crashed."""
	logger = frappe.logger("woo_prime", allow_site=True)
	logger.error(f"{title}: {message}")

	try:
		frappe.log_error(title=title, message=message)
	except Exception as e:
		err_str = str(e)
		if "144" in err_str or "marked as crashed" in err_str.lower():
			try:
				repair_crashed_tables()
				frappe.log_error(title=title, message=message)
			except Exception:
				pass


def clean_text(text):
	"""Decode HTML entities and clean text."""
	if not text:
		return ""
	return html.unescape(str(text)).strip()


def generate_unique_abbr(name, existing_abbrs, max_len=10):
	"""Generate a unique abbreviation for ERPNext Item Attribute Value."""
	clean_name = clean_text(name)
	# Remove non-alphanumeric except hyphens/underscores
	alphanumeric = re.sub(r"[^\w\s-]", "", clean_name).strip()
	base = alphanumeric[: max(1, max_len - 2)].strip().upper() if alphanumeric else "VAL"

	candidate = base[:max_len]
	counter = 1
	while candidate.lower() in existing_abbrs:
		suffix = str(counter)
		allowed_base = max(1, max_len - len(suffix))
		candidate = f"{base[:allowed_base]}{suffix}"
		counter += 1

	existing_abbrs.add(candidate.lower())
	return candidate

