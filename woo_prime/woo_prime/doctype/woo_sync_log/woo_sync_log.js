// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Woo Sync Log", {
	refresh(frm) {
		if (frm.doc.status === "Failed") {
			frm.add_custom_button(
				__("Retry Sync"),
				function () {
					frappe.call({
						method: "retry_sync",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Retrying sync operation..."),
						callback: function (r) {
							frm.reload_doc();
						},
					});
				}
			).addClass("btn-danger");
		}

		// Ensure Request / Response Data section is expanded and visible
		if (frm.fields_dict.data_section) {
			frm.fields_dict.data_section.collapse(false);
		}
		if (frm.fields_dict.error_section && frm.doc.status === "Failed") {
			frm.fields_dict.error_section.collapse(false);
		}
	},
});

frappe.listview_settings["Woo Sync Log"] = {
	get_indicator: function (doc) {
		if (doc.status === "Success") {
			return [__("Success"), "green", "status,=,Success"];
		} else if (doc.status === "Failed") {
			return [__("Failed"), "red", "status,=,Failed"];
		}
		return [__("Queued"), "orange", "status,=,Queued"];
	},
};
