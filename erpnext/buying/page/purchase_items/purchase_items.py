import frappe

@frappe.whitelist()
def get_invoices():
    try:
        # Use frappe.get_list to query the Purchase Invoice DocType
        invoices = frappe.get_list(
            "Purchase Invoice",
            fields=["*"],
            limit=10,
            order_by="creation desc"
        )
        return invoices
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error fetching invoices")
        return {"error": str(e)}

import frappe

@frappe.whitelist()
def check_link():
    """
    A simple function to confirm the Python controller is working.
    """
    return "Python controller is linked!"