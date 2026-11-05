// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Ignored Item", {
	refresh(frm) {
		// Add button to remove tombstone (restore item)
		if (!frm.is_new()) {
			frm.add_custom_button(__("Remove (Allow Restore)"), function() {
				frappe.confirm(
					__("Remove this tombstone? The item can be synced again."),
					function() {
						frappe.call({
							method: "woo_prime.woo_prime.doctype.woo_ignored_item.woo_ignored_item.remove_tombstone",
							args: {
								item_code: frm.doc.item_code,
								sku: frm.doc.sku
							},
							callback: function() {
								frappe.msgprint(__("Tombstone removed. Item can now be synced."));
								frappe.set_route("List", "Woo Ignored Item");
							}
						});
					}
				);
			});
		}
	}
});
