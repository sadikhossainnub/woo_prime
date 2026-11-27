import frappe


def execute():
	"""Backfill categories_display from Woo Item Category child table for List View."""
	if not frappe.db.has_column("Woo Item", "categories_display"):
		return

	rows = frappe.get_all(
		"Woo Item Category",
		filters={"parenttype": "Woo Item"},
		fields=["parent", "category"],
		order_by="idx asc",
	)

	by_parent = {}
	for r in rows:
		if r.parent and r.category:
			by_parent.setdefault(r.parent, []).append(r.category)

	for parent, cats in by_parent.items():
		frappe.db.set_value(
			"Woo Item",
			parent,
			"categories_display",
			", ".join(cats),
			update_modified=False,
		)
