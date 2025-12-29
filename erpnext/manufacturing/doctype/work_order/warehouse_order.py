import json
import os
import frappe
from frappe.model.document import Document

# Path to warehouse flow JSON
WAREHOUSE_FLOW_PATH = os.path.join(
	frappe.get_app_path("erpnext"),
	"manufacturing",
	"doctype",
	"work_order",
	"warehouse_flow.json"
)

@frappe.whitelist()
def get_warehouse_flow():
	"""
	Returns warehouse flow JSON
	Used by client-side JS
	"""
	if not os.path.exists(WAREHOUSE_FLOW_PATH):
		return {}

	with open(WAREHOUSE_FLOW_PATH, "r") as f:
		return json.load(f)


def apply_warehouse_flow_to_work_order(doc: Document):

	# Safety checks
	if not doc.production_item:
		return

	flow = get_warehouse_flow()
	if not flow:
		return

	#  MATCH USING ITEM NAME 
	item_name = doc.production_item.strip().lower()

	mapping = None
	for key, value in flow.items():
		if key.strip().lower() == item_name:
			mapping = value
			break

	if not mapping:
		return

	# ===============================
	# Apply header warehouses
	# ===============================
	if mapping.get("source_warehouse"):
		doc.source_warehouse = mapping.get("source_warehouse")

	if mapping.get("wip_warehouse"):
		doc.wip_warehouse = mapping.get("wip_warehouse")

	if mapping.get("target_warehouse"):
		doc.fg_warehouse = mapping.get("target_warehouse")

	# ===============================
	# Apply to Required Items
	# ===============================
	if mapping.get("source_warehouse"):
		for row in doc.required_items or []:
			row.source_warehouse = mapping.get("source_warehouse")
