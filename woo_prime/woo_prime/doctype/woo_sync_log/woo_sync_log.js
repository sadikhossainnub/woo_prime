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
