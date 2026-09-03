// Copyright (c) 2026, prime tech bd and contributors
// For license information, please see license.txt

frappe.treeview_settings["Woo Category"] = {
	breadcrumb: "Woo Prime",
	title: __("Woo Categories"),
	get_tree_root: true,
	root_label: "All Woo Categories",
	get_tree_nodes: "woo_prime.woo_prime.doctype.woo_category.woo_category.get_children",
	add_tree_node: "woo_prime.woo_prime.doctype.woo_category.woo_category.add_node",
	filters: [],
	fields: [
		{
			fieldtype: "Data",
			fieldname: "category_name",
			label: __("Category Name"),
			reqd: 1,
		},
		{
			fieldtype: "Check",
			fieldname: "is_group",
			label: __("Is Group"),
			description: __("Check if this category will have sub-categories"),
		},
	],
	toolbar: [
		{
			label: __("Push to WooCommerce"),
			condition: function (node) {
				return !node.is_root;
			},
			click: function (node) {
				frappe.call({
					method: "woo_prime.woo_prime.doctype.woo_category.woo_category.push_category_to_woo",
					args: {
						doc_name: node.label,
					},
					freeze: true,
					freeze_message: __("Pushing Category to WooCommerce..."),
					callback: function (r) {
						cur_tree && cur_tree.make_tree();
					},
				});
			},
			btnClass: "btn-primary",
		},
	],
	menu_items: [
		{
			label: __("Fetch from WooCommerce"),
			action: function () {
				frappe.call({
					method: "woo_prime.woo_prime.doctype.woo_category.woo_category.sync_categories_from_woo",
					freeze: true,
					freeze_message: __("Fetching product categories from WooCommerce..."),
					callback: function (r) {
						if (r && r.message) {
							frappe.show_alert({
								message: __("Synced {0} categories!", [r.message]),
								indicator: "green",
							});
						}
						cur_tree && cur_tree.make_tree();
					},
				});
			},
		},
	],
	onrender(node) {
		// Show WooCommerce Category ID badge on each node
		if (node.data && node.data.woo_category_id) {
			$(`<span class="badge badge-secondary" style="margin-left: 8px; font-size: 10px; background: #6c757d; color: #fff; border-radius: 4px; padding: 2px 6px;">
				WC #${node.data.woo_category_id}
			</span>`).appendTo(node.$tree_link);
		} else if (!node.is_root) {
			$(`<span class="badge badge-warning" style="margin-left: 8px; font-size: 10px; background: #ffc107; color: #000; border-radius: 4px; padding: 2px 6px;">
				Not Pushed
			</span>`).appendTo(node.$tree_link);
		}
	},
};
