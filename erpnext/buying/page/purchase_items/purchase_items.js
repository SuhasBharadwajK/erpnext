frappe.pages['purchase-items'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'None',
		single_column: true
	});

	// Call the server-side function to check if the link is working
    frappe.call({
        method: "erpnext.buying.page.purchase_items.purchase_items.get_invoices",
        callback: function(r) {
            if (r.message) {
                console.log("Server returned message:", r.message);
                frappe.msgprint(r.message);
            } else {
                frappe.msgprint("Error: Could not get a response from the server.");
            }
        }
    });
}