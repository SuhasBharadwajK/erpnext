# Copyright (c) 2017, Frappe Technelogies Pvt. Ltd. and contributors
# For license information, please see license.txt


import copy
import json

import frappe
from frappe import _, msgprint
from frappe.model.document import Document
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils import (
	add_days,
	ceil,
	cint,
	comma_and,
	flt,
	get_link_to_form,
	getdate,
	nowdate,
)
from frappe.utils.csvutils import build_csv_response
from pypika.terms import ExistsCriterion

from erpnext.manufacturing.doctype.bom.bom import get_children as get_bom_children
from erpnext.manufacturing.doctype.bom.bom import validate_bom_no
from erpnext.manufacturing.doctype.work_order.work_order import get_item_details
from erpnext.setup.doctype.item_group.item_group import get_item_group_defaults
from erpnext.stock.get_item_details import get_conversion_factor
from erpnext.stock.utils import get_or_make_bin
from erpnext.utilities.transaction_base import validate_uom_is_integer


class ProductionPlan(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from erpnext.manufacturing.doctype.material_request_plan_item.material_request_plan_item import (
			MaterialRequestPlanItem,
		)
		from erpnext.manufacturing.doctype.production_plan_item.production_plan_item import ProductionPlanItem
		from erpnext.manufacturing.doctype.production_plan_item_reference.production_plan_item_reference import (
			ProductionPlanItemReference,
		)
		from erpnext.manufacturing.doctype.production_plan_material_request.production_plan_material_request import (
			ProductionPlanMaterialRequest,
		)
		from erpnext.manufacturing.doctype.production_plan_material_request_warehouse.production_plan_material_request_warehouse import (
			ProductionPlanMaterialRequestWarehouse,
		)
		from erpnext.manufacturing.doctype.production_plan_sales_order.production_plan_sales_order import (
			ProductionPlanSalesOrder,
		)
		from erpnext.manufacturing.doctype.production_plan_sub_assembly_item.production_plan_sub_assembly_item import (
			ProductionPlanSubAssemblyItem,
		)
		from frappe.types import DF

		amended_from: DF.Link | None
		combine_items_line_1: DF.Check
		combine_items_line_2: DF.Check
		combine_items_line_3: DF.Check
		combine_items_mono_line: DF.Check
		combine_items_multi_line: DF.Check
		combine_sub_items: DF.Check
		company: DF.Link
		consider_minimum_order_qty: DF.Check
		customer: DF.Link | None
		deleted_job_card_count: DF.Int
		for_warehouse: DF.Link | None
		from_date: DF.Date | None
		from_delivery_date: DF.Date | None
		get_items_from: DF.Literal["", "Sales Order", "Material Request"]
		ignore_existing_ordered_qty: DF.Check
		include_non_stock_items: DF.Check
		include_safety_stock: DF.Check
		include_subcontracted_items: DF.Check
		is_monthly_production_plan: DF.Check
		is_parent_plan: DF.Check
		is_test_item: DF.Check
		is_work_order_created: DF.Check
		item_code: DF.Link | None
		material_requests: DF.Table[ProductionPlanMaterialRequest]
		monthly_production_plan: DF.Link | None
		mr_items: DF.Table[MaterialRequestPlanItem]
		naming_series: DF.Literal["MFG-DPP-.YYYY.-", "MFG-MPP-.YYYY.-"]
		po_items_line_1: DF.Table[ProductionPlanItem]
		po_items_line_2: DF.Table[ProductionPlanItem]
		po_items_line_3: DF.Table[ProductionPlanItem]
		po_items_mono_line: DF.Table[ProductionPlanItem]
		po_items_multi_line: DF.Table[ProductionPlanItem]
		posting_date: DF.Date
		prod_plan_references: DF.Table[ProductionPlanItemReference]
		project: DF.Link | None
		reason_for_deletion_of_job_cards: DF.Data | None
		sales_order_status: DF.Literal["", "To Deliver and Bill", "To Bill", "To Deliver"]
		sales_orders: DF.Table[ProductionPlanSalesOrder]
		skip_available_sub_assembly_item: DF.Check
		status: DF.Literal[
			"",
			"Draft",
			"Submitted",
			"Not Started",
			"In Process",
			"Completed",
			"Closed",
			"Cancelled",
			"Material Requested",
		]
		sub_assembly_items: DF.Table[ProductionPlanSubAssemblyItem]
		sub_assembly_warehouse: DF.Link | None
		to_date: DF.Date | None
		to_delivery_date: DF.Date | None
		total_planned_qty: DF.Float
		total_produced_qty: DF.Float
		warehouse: DF.Link | None
		warehouses: DF.TableMultiSelect[ProductionPlanMaterialRequestWarehouse]
	# end: auto-generated types

	def validate(self):
		self.set_pending_qty_in_row_without_reference()
		self.calculate_total_planned_qty()
		self.set_status()
		self._rename_temporary_references()
		validate_uom_is_integer(self, "stock_uom", "planned_qty")
		self.validate_sales_orders()
		self.validate_material_request_type()

	def validate_material_request_type(self):
		for row in self.get("mr_items"):
			if row.from_warehouse and row.material_request_type != "Material Transfer":
				row.from_warehouse = ""

	@frappe.whitelist()
	def validate_sales_orders(self, sales_order=None):
		sales_orders = []

		if sales_order:
			sales_orders.append(sales_order)
		else:
			sales_orders = [row.sales_order for row in self.sales_orders if row.sales_order]

		data = sales_order_query(filters={"company": self.company, "sales_orders": sales_orders})

		title = _("Production Plan Already Submitted")
		if not data and sales_orders:
			msg = _("No items are available in the sales order {0} for production").format(sales_orders[0])
			if len(sales_orders) > 1:
				sales_orders = ", ".join(sales_orders)
				msg = _("No items are available in sales orders {0} for production").format(sales_orders)

			frappe.throw(msg, title=title)

		data = [d[0] for d in data]

		for sales_order in sales_orders:
			if sales_order not in data:
				frappe.throw(
					_("No items are available in the sales order {0} for production").format(sales_order),
					title=title,
				)

	@frappe.whitelist()
	def get_open_sales_orders(self):
		"""Pull sales orders  which are pending to deliver based on criteria selected"""
		open_so = get_sales_orders(self)

		if open_so:
			self.add_so_in_table(open_so)
		else:
			frappe.msgprint(_("Sales orders are not available for production"))

	def add_so_in_table(self, open_so):
		"""Add sales orders in the table"""
		self.set("sales_orders", [])

		for data in open_so:
			self.append(
				"sales_orders",
				{
					"sales_order": data.name,
					"sales_order_date": data.transaction_date,
					"customer": data.customer,
					"grand_total": data.base_grand_total,
				},
			)

	@frappe.whitelist()
	def get_pending_material_requests(self):
		"""Pull Material Requests that are pending based on criteria selected"""

		bom = frappe.qb.DocType("BOM")
		mr = frappe.qb.DocType("Material Request")
		mr_item = frappe.qb.DocType("Material Request Item")

		pending_mr_query = (
			frappe.qb.from_(mr)
			.from_(mr_item)
			.select(mr.name, mr.transaction_date)
			.distinct()
			.where(
				(mr_item.parent == mr.name)
				& (mr.material_request_type == "Manufacture")
				& (mr.docstatus == 1)
				& (mr.status != "Stopped")
				& (mr.company == self.company)
				& (mr_item.qty > IfNull(mr_item.ordered_qty, 0))
				& (
					ExistsCriterion(
						frappe.qb.from_(bom)
						.select(bom.name)
						.where((bom.item == mr_item.item_code) & (bom.is_active == 1))
					)
				)
			)
		)

		if self.from_date:
			pending_mr_query = pending_mr_query.where(mr.transaction_date >= self.from_date)

		if self.to_date:
			pending_mr_query = pending_mr_query.where(mr.transaction_date <= self.to_date)

		if self.warehouse:
			pending_mr_query = pending_mr_query.where(mr_item.warehouse == self.warehouse)

		if self.item_code:
			pending_mr_query = pending_mr_query.where(mr_item.item_code == self.item_code)

		pending_mr = pending_mr_query.run(as_dict=True)

		self.add_mr_in_table(pending_mr)

	def add_mr_in_table(self, pending_mr):
		"""Add Material Requests in the table"""
		self.set("material_requests", [])

		for data in pending_mr:
			self.append(
				"material_requests",
				{"material_request": data.name, "material_request_date": data.transaction_date},
			)

	def get_so_mr_list(self, field, table):
		"""Returns a list of Sales Orders or Material Requests from the respective tables"""
		so_mr_list = [d.get(field) for d in self.get(table) if d.get(field)]
		return so_mr_list

	def get_bom_item_condition(self):
		"""Check if Item or if its Template has a BOM."""
		bom_item_condition = None
		has_bom = frappe.db.exists({"doctype": "BOM", "item": self.item_code, "docstatus": 1})

		if not has_bom:
			bom = frappe.qb.DocType("BOM")
			template_item = frappe.db.get_value("Item", self.item_code, ["variant_of"])
			bom_item_condition = bom.item == template_item or None

		return bom_item_condition

	def get_so_items(self):
		# Check for empty table or empty rows
		if not self.get("sales_orders") or not self.get_so_mr_list("sales_order", "sales_orders"):
			frappe.throw(_("Please fill the Sales Orders table"), title=_("Sales Orders Required"))

		so_list = self.get_so_mr_list("sales_order", "sales_orders")

		bom = frappe.qb.DocType("BOM")
		so_item = frappe.qb.DocType("Sales Order Item")

		items_subquery = frappe.qb.from_(bom).select(bom.name).where(bom.is_active == 1)
		items_query = (
			frappe.qb.from_(so_item)
			.select(
				so_item.parent,
				so_item.item_code,
				so_item.warehouse,
				so_item.qty,
				so_item.work_order_qty,
				so_item.delivered_qty,
				so_item.conversion_factor,
				so_item.description,
				so_item.name,
				so_item.bom_no,
			)
			.distinct()
			.where(
				(so_item.parent.isin(so_list))
				& (so_item.docstatus == 1)
				& (so_item.qty > so_item.work_order_qty)
			)
		)

		if self.item_code and frappe.db.exists("Item", self.item_code):
			items_query = items_query.where(so_item.item_code == self.item_code)
			items_subquery = items_subquery.where(
				self.get_bom_item_condition() or bom.item == so_item.item_code
			)

		items_query = items_query.where(ExistsCriterion(items_subquery))

		items = items_query.run(as_dict=True)

		for item in items:
			item.pending_qty = (
				flt(item.qty) - max(item.work_order_qty, item.delivered_qty, 0)
			) * item.conversion_factor

		pi = frappe.qb.DocType("Packed Item")

		pending_qty = (
			frappe.qb.terms.Case()
			.when(
				(so_item.work_order_qty > so_item.delivered_qty),
				(((so_item.qty - so_item.work_order_qty) * pi.qty) / so_item.qty),
			)
			.else_(((so_item.qty - so_item.delivered_qty) * pi.qty) / so_item.qty)
		)

		packed_items_query = (
			frappe.qb.from_(so_item)
			.from_(pi)
			.select(
				pi.parent,
				pi.item_code,
				pi.warehouse.as_("warehouse"),
				pending_qty.as_("pending_qty"),
				pi.parent_item,
				pi.description,
				so_item.name,
			)
			.distinct()
			.where(
				(so_item.parent == pi.parent)
				& (so_item.docstatus == 1)
				& (pi.parent_item == so_item.item_code)
				& (so_item.parent.isin(so_list))
				& (
					(
						(so_item.work_order_qty > so_item.delivered_qty)
						& (so_item.qty > so_item.work_order_qty)
					)
					| (
						(so_item.work_order_qty <= so_item.delivered_qty)
						& (so_item.qty > so_item.delivered_qty)
					)
				)
				& (
					ExistsCriterion(
						frappe.qb.from_(bom)
						.select(bom.name)
						.where((bom.item == pi.item_code) & (bom.is_active == 1))
					)
				)
			)
		)

		if self.item_code:
			packed_items_query = packed_items_query.where(so_item.item_code == self.item_code)

		packed_items = packed_items_query.run(as_dict=True)

		self.add_items(items + packed_items)
		self.calculate_total_planned_qty()

	def get_mr_items(self):
		# Check for empty table or empty rows
		if not self.get("material_requests") or not self.get_so_mr_list(
			"material_request", "material_requests"
		):
			frappe.throw(_("Please fill the Material Requests table"), title=_("Material Requests Required"))

		mr_list = self.get_so_mr_list("material_request", "material_requests")

		bom = frappe.qb.DocType("BOM")
		mr_item = frappe.qb.DocType("Material Request Item")

		items_query = (
			frappe.qb.from_(mr_item)
			.select(
				mr_item.parent,
				mr_item.name,
				mr_item.item_code,
				mr_item.warehouse,
				mr_item.description,
				mr_item.bom_no,
				((mr_item.qty - mr_item.ordered_qty) * mr_item.conversion_factor).as_("pending_qty"),
			)
			.distinct()
			.where(
				(mr_item.parent.isin(mr_list))
				& (mr_item.docstatus == 1)
				& (mr_item.qty > mr_item.ordered_qty)
				& (
					ExistsCriterion(
						frappe.qb.from_(bom)
						.select(bom.name)
						.where((bom.item == mr_item.item_code) & (bom.is_active == 1))
					)
				)
			)
		)

		if self.item_code:
			items_query = items_query.where(mr_item.item_code == self.item_code)

		items = items_query.run(as_dict=True)

		self.add_items(items)
		self.calculate_total_planned_qty()

	def add_pp_ref(self, refs):
		for bom_no in refs:
			for so_detail in refs[bom_no]["so_details"]:
				self.append(
					"prod_plan_references",
					{
						"item_reference": refs[bom_no]["po_item_ref"],
						"sales_order": so_detail["sales_order"],
						"sales_order_item": so_detail["sales_order_item"],
						"qty": so_detail["qty"],
					},
				)

	def on_submit(self):
		self.update_bin_qty()
		self.update_sales_order()

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self.delete_draft_work_order()
		self.update_bin_qty()
		self.update_sales_order()

	@staticmethod
	def get_so_wise_planned_qty(sales_orders):
		so_wise_planned_qty = frappe._dict()
		data = frappe.get_all(
			"Production Plan Item",
			fields=["sales_order", "sales_order_item", "SUM(planned_qty) as qty"],
			filters={
				"sales_order": ("in", sales_orders),
				"docstatus": 1,
				"sales_order_item": ("is", "set"),
			},
			group_by="sales_order, sales_order_item",
		)

		for row in data:
			key = (row.sales_order, row.sales_order_item)
			so_wise_planned_qty[key] = row.qty

		return so_wise_planned_qty

	def update_bin_qty(self):
		for d in self.mr_items:
			if d.warehouse:
				bin_name = get_or_make_bin(d.item_code, d.warehouse)
				bin = frappe.get_doc("Bin", bin_name, for_update=True)
				bin.update_reserved_qty_for_production_plan()

		for d in self.sub_assembly_items:
			if d.fg_warehouse and d.type_of_manufacturing == "In House":
				bin_name = get_or_make_bin(d.production_item, d.fg_warehouse)
				bin = frappe.get_doc("Bin", bin_name, for_update=True)
				bin.update_reserved_qty_for_for_sub_assembly()

	def delete_draft_work_order(self):
		for d in frappe.get_all(
			"Work Order", fields=["name"], filters={"docstatus": 0, "production_plan": ("=", self.name)}
		):
			frappe.delete_doc("Work Order", d.name)

	@frappe.whitelist()
	def set_status(self, close=None, update_bin=False):
		self.status = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(self.docstatus)

		if close:
			self.db_set("status", "Closed")
			self.update_bin_qty()
			return

		if self.total_produced_qty > 0:
			self.status = "In Process"
			if self.all_items_completed():
				self.status = "Completed"

		if self.status != "Completed":
			self.update_ordered_status()
			self.update_requested_status()

		if close is not None:
			self.db_set("status", self.status)

		if update_bin and self.docstatus == 1 and self.status != "Completed":
			self.update_bin_qty()

	def update_requested_status(self):
		if not self.mr_items:
			return

		update_status = True
		for d in self.mr_items:
			if d.quantity != d.requested_qty:
				update_status = False

		if update_status:
			self.status = "Material Requested"

	def make_work_order_for_subassembly_items(self, wo_list, subcontracted_po, default_warehouses):
		for row in self.sub_assembly_items:
			if row.type_of_manufacturing == "Subcontract":
				subcontracted_po.setdefault(row.supplier, []).append(row)
				continue

			if row.type_of_manufacturing == "Material Request":
				continue

			work_order_data = {
				"wip_warehouse": default_warehouses.get("wip_warehouse"),
				"fg_warehouse": default_warehouses.get("fg_warehouse"),
				"company": self.get("company"),
			}

			if flt(row.qty) <= flt(row.ordered_qty):
				continue

			self.prepare_data_for_sub_assembly_items(row, work_order_data)

			if work_order_data.get("qty") <= 0:
				continue

			work_order = self.create_work_order(work_order_data)
			if work_order:
				wo_list.append(work_order)

	def make_subcontracted_purchase_order(self, subcontracted_po, purchase_orders):
		if not subcontracted_po:
			return

		for supplier, po_list in subcontracted_po.items():
			po = frappe.new_doc("Purchase Order")
			po.company = self.company
			po.supplier = supplier
			po.schedule_date = getdate(po_list[0].schedule_date) if po_list[0].schedule_date else nowdate()
			po.is_subcontracted = 1
			for row in po_list:
				po_data = {
					"fg_item": row.production_item,
					"warehouse": row.fg_warehouse,
					"production_plan_sub_assembly_item": row.name,
					"bom": row.bom_no,
					"production_plan": self.name,
					"fg_item_qty": row.qty,
				}

				for field in [
					"schedule_date",
					"qty",
					"description",
					"production_plan_item",
				]:
					po_data[field] = row.get(field)

				po.append("items", po_data)

			po.set_service_items_for_finished_goods()
			po.set_missing_values()
			po.flags.ignore_mandatory = True
			po.flags.ignore_validate = True
			po.insert()
			purchase_orders.append(po.name)

	def show_list_created_message(self, doctype, doc_list=None):
		if not doc_list:
			return

		frappe.flags.mute_messages = False
		if doc_list:
			doc_list = [get_link_to_form(doctype, p) for p in doc_list]
			msgprint(_("{0} created").format(comma_and(doc_list)))

	@frappe.whitelist()
	def make_material_request(self):
		"""Create Material Requests grouped by Sales Order and Material Request Type"""
		material_request_list = []
		material_request_map = {}

		for item in self.mr_items:
			item_doc = frappe.get_cached_doc("Item", item.item_code)

			material_request_type = item.material_request_type or item_doc.default_material_request_type

			# key for Sales Order:Material Request Type:Customer
			key = "{}:{}:{}".format(item.sales_order, material_request_type, item_doc.customer or "")
			schedule_date = item.schedule_date or add_days(nowdate(), cint(item_doc.lead_time_days))

			if key not in material_request_map:
				# make a new MR for the combination
				material_request_map[key] = frappe.new_doc("Material Request")
				material_request = material_request_map[key]
				material_request.update(
					{
						"transaction_date": nowdate(),
						"status": "Draft",
						"company": self.company,
						"material_request_type": material_request_type,
						"customer": item_doc.customer or "",
					}
				)
				material_request_list.append(material_request)
			else:
				material_request = material_request_map[key]

			# add item
			material_request.append(
				"items",
				{
					"item_code": item.item_code,
					"from_warehouse": item.from_warehouse
					if material_request_type == "Material Transfer"
					else None,
					"qty": item.quantity,
					"schedule_date": schedule_date,
					"warehouse": item.warehouse,
					"sales_order": item.sales_order,
					"production_plan": self.name,
					"material_request_plan_item": item.name,
					"project": frappe.db.get_value("Sales Order", item.sales_order, "project")
					if item.sales_order
					else None,
				},
			)

		for material_request in material_request_list:
			# submit
			material_request.flags.ignore_permissions = 1
			material_request.run_method("set_missing_values")

			material_request.save()
			if self.get("submit_material_request"):
				material_request.submit()

		frappe.flags.mute_messages = False

		if material_request_list:
			material_request_list = [
				get_link_to_form("Material Request", m.name) for m in material_request_list
			]
			msgprint(_("{0} created").format(comma_and(material_request_list)))
		else:
			msgprint(_("No material request created"))

	def set_default_supplier_for_subcontracting_order(self):
		items = [
			d.production_item for d in self.sub_assembly_items if d.type_of_manufacturing == "Subcontract"
		]

		if not items:
			return

		default_supplier = frappe._dict(
			frappe.get_all(
				"Item Default",
				fields=["parent", "default_supplier"],
				filters={"parent": ("in", items), "default_supplier": ("is", "set")},
				as_list=1,
			)
		)

		if not default_supplier:
			return

		for row in self.sub_assembly_items:
			if row.type_of_manufacturing != "Subcontract":
				continue

			row.supplier = default_supplier.get(row.production_item)

@frappe.whitelist()
def download_raw_materials(doc, warehouses=None):
	if isinstance(doc, str):
		doc = frappe._dict(json.loads(doc))

	item_list = [
		[
			"Item Code",
			"Item Name",
			"Description",
			"Stock UOM",
			"Warehouse",
			"Required Qty as per BOM",
			"Projected Qty",
			"Available Qty In Hand",
			"Ordered Qty",
			"Planned Qty",
			"Reserved Qty for Production",
			"Safety Stock",
			"Required Qty",
		]
	]

	doc.warehouse = None
	frappe.flags.show_qty_in_stock_uom = 1
	items = get_items_for_material_requests(doc, warehouses=warehouses, get_parent_warehouse_data=True)

	duplicate_item_wh_list = frappe._dict()

	for d in items:
		key = (d.get("item_code"), d.get("warehouse"))
		if key in duplicate_item_wh_list:
			rm_data = duplicate_item_wh_list[key]
			rm_data[12] += d.get("quantity")
			continue

		rm_data = [
			d.get("item_code"),
			d.get("item_name"),
			d.get("description"),
			d.get("stock_uom"),
			d.get("warehouse"),
			d.get("required_bom_qty"),
			d.get("projected_qty"),
			d.get("actual_qty"),
			d.get("ordered_qty"),
			d.get("planned_qty"),
			d.get("reserved_qty_for_production"),
			d.get("safety_stock"),
			d.get("quantity"),
		]

		duplicate_item_wh_list[key] = rm_data
		item_list.append(rm_data)

		if not doc.get("for_warehouse"):
			row = {"item_code": d.get("item_code")}
			for bin_dict in get_bin_details(row, doc.company, all_warehouse=True):
				if d.get("warehouse") == bin_dict.get("warehouse"):
					continue

				item_list.append(
					[
						"",
						"",
						"",
						bin_dict.get("warehouse"),
						"",
						bin_dict.get("projected_qty", 0),
						bin_dict.get("actual_qty", 0),
						bin_dict.get("ordered_qty", 0),
						bin_dict.get("reserved_qty_for_production", 0),
					]
				)

	build_csv_response(item_list, doc.name)


def get_exploded_items(item_details, company, bom_no, include_non_stock_items, planned_qty=1, doc=None):
	bei = frappe.qb.DocType("BOM Explosion Item")
	bom = frappe.qb.DocType("BOM")
	item = frappe.qb.DocType("Item")
	item_default = frappe.qb.DocType("Item Default")
	item_uom = frappe.qb.DocType("UOM Conversion Detail")

	data = (
		frappe.qb.from_(bei)
		.join(bom)
		.on(bom.name == bei.parent)
		.join(item)
		.on(item.name == bei.item_code)
		.left_join(item_default)
		.on((item_default.parent == item.name) & (item_default.company == company))
		.left_join(item_uom)
		.on((item.name == item_uom.parent) & (item_uom.uom == item.purchase_uom))
		.select(
			(IfNull(Sum(bei.stock_qty / IfNull(bom.quantity, 1)), 0) * planned_qty).as_("qty"),
			item.item_name,
			item.name.as_("item_code"),
			bei.description,
			bei.stock_uom,
			item.min_order_qty,
			bei.source_warehouse,
			item.default_material_request_type,
			item.min_order_qty,
			item_default.default_warehouse,
			item.purchase_uom,
			item_uom.conversion_factor,
			item.safety_stock,
			bom.item.as_("main_bom_item"),
		)
		.where(
			(bei.docstatus < 2)
			& (bom.name == bom_no)
			& (item.is_stock_item.isin([0, 1]) if include_non_stock_items else item.is_stock_item == 1)
		)
		.groupby(bei.item_code, bei.stock_uom)
	).run(as_dict=True)

	for d in data:
		if not d.conversion_factor and d.purchase_uom:
			d.conversion_factor = get_uom_conversion_factor(d.item_code, d.purchase_uom)
		item_details.setdefault(d.get("item_code"), d)

	return item_details


def get_uom_conversion_factor(item_code, uom):
	return frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
	)


def get_subitems(
	doc,
	data,
	item_details,
	bom_no,
	company,
	include_non_stock_items,
	include_subcontracted_items,
	parent_qty,
	planned_qty=1,
):
	bom_item = frappe.qb.DocType("BOM Item")
	bom = frappe.qb.DocType("BOM")
	item = frappe.qb.DocType("Item")
	item_default = frappe.qb.DocType("Item Default")
	item_uom = frappe.qb.DocType("UOM Conversion Detail")

	items = (
		frappe.qb.from_(bom_item)
		.join(bom)
		.on(bom.name == bom_item.parent)
		.join(item)
		.on(bom_item.item_code == item.name)
		.left_join(item_default)
		.on((item.name == item_default.parent) & (item_default.company == company))
		.left_join(item_uom)
		.on((item.name == item_uom.parent) & (item_uom.uom == item.purchase_uom))
		.select(
			bom_item.item_code,
			item.default_material_request_type,
			item.item_name,
			IfNull(parent_qty * Sum(bom_item.stock_qty / IfNull(bom.quantity, 1)) * planned_qty, 0).as_(
				"qty"
			),
			item.is_sub_contracted_item.as_("is_sub_contracted"),
			bom_item.source_warehouse,
			item.default_bom.as_("default_bom"),
			bom_item.description.as_("description"),
			bom_item.stock_uom.as_("stock_uom"),
			item.min_order_qty.as_("min_order_qty"),
			item.safety_stock.as_("safety_stock"),
			item_default.default_warehouse,
			item.purchase_uom,
			item_uom.conversion_factor,
			bom.item.as_("main_bom_item"),
		)
		.where(
			(bom.name == bom_no)
			& (bom_item.docstatus < 2)
			& (item.is_stock_item.isin([0, 1]) if include_non_stock_items else item.is_stock_item == 1)
		)
		.groupby(bom_item.item_code)
	).run(as_dict=True)

	for d in items:
		if not data.get("include_exploded_items") or not d.default_bom:
			if d.item_code in item_details:
				item_details[d.item_code].qty = item_details[d.item_code].qty + d.qty
			else:
				if not d.conversion_factor and d.purchase_uom:
					d.conversion_factor = get_uom_conversion_factor(d.item_code, d.purchase_uom)

				item_details[d.item_code] = d

		if data.get("include_exploded_items") and d.default_bom:
			if (
				d.default_material_request_type in ["Manufacture", "Purchase"] and not d.is_sub_contracted
			) or (d.is_sub_contracted and include_subcontracted_items):
				if d.qty > 0:
					get_subitems(
						doc,
						data,
						item_details,
						d.default_bom,
						company,
						include_non_stock_items,
						include_subcontracted_items,
						d.qty,
					)
	return item_details


def get_material_request_items(
	doc,
	row,
	sales_order,
	company,
	ignore_existing_ordered_qty,
	include_safety_stock,
	warehouse,
	bin_dict,
	consumed_qty,
):
	required_qty = 0
	item_code = row.get("item_code")

	if ignore_existing_ordered_qty or bin_dict.get("projected_qty", 0) < 0:
		required_qty = flt(row.get("qty"))
	else:
		key = (item_code, warehouse)
		available_qty = flt(bin_dict.get("projected_qty", 0)) - consumed_qty[key]
		if available_qty > 0:
			required_qty = max(0, flt(row.get("qty")) - available_qty)
			consumed_qty[key] += min(flt(row.get("qty")), available_qty)
		else:
			required_qty = flt(row.get("qty"))

	if doc.get("consider_minimum_order_qty") and required_qty > 0 and required_qty < row["min_order_qty"]:
		required_qty = row["min_order_qty"]

	item_group_defaults = get_item_group_defaults(row.item_code, company)

	if not row["purchase_uom"]:
		row["purchase_uom"] = row["stock_uom"]

	if row["purchase_uom"] != row["stock_uom"]:
		if not (row["conversion_factor"] or frappe.flags.show_qty_in_stock_uom):
			frappe.throw(
				_("UOM Conversion factor ({0} -> {1}) not found for item: {2}").format(
					row["purchase_uom"], row["stock_uom"], row.item_code
				)
			)

			required_qty = required_qty / row["conversion_factor"]

	if frappe.db.get_value("UOM", row["purchase_uom"], "must_be_whole_number"):
		required_qty = ceil(required_qty)

	if include_safety_stock:
		required_qty += flt(row["safety_stock"])

	item_details = frappe.get_cached_value("Item", row.item_code, ["purchase_uom", "stock_uom"], as_dict=1)

	conversion_factor = 1.0
	if (
		row.get("default_material_request_type") == "Purchase"
		and item_details.purchase_uom
		and item_details.purchase_uom != item_details.stock_uom
	):
		conversion_factor = (
			get_conversion_factor(row.item_code, item_details.purchase_uom).get("conversion_factor") or 1.0
		)

	if required_qty > 0:
		return {
			"item_code": row.item_code,
			"item_name": row.item_name,
			"quantity": required_qty / conversion_factor,
			"conversion_factor": conversion_factor,
			"required_bom_qty": row.get("qty"),
			"stock_uom": row.get("stock_uom"),
			"warehouse": warehouse
			or row.get("source_warehouse")
			or row.get("default_warehouse")
			or item_group_defaults.get("default_warehouse"),
			"safety_stock": row.safety_stock,
			"actual_qty": bin_dict.get("actual_qty", 0),
			"projected_qty": bin_dict.get("projected_qty", 0),
			"ordered_qty": bin_dict.get("ordered_qty", 0),
			"reserved_qty_for_production": bin_dict.get("reserved_qty_for_production", 0),
			"min_order_qty": row["min_order_qty"],
			"material_request_type": row.get("default_material_request_type"),
			"sales_order": sales_order,
			"description": row.get("description"),
			"uom": row.get("purchase_uom") or row.get("stock_uom"),
			"main_bom_item": row.get("main_bom_item"),
		}


def get_sales_orders(self):
	bom = frappe.qb.DocType("BOM")
	pi = frappe.qb.DocType("Packed Item")
	so = frappe.qb.DocType("Sales Order")
	so_item = frappe.qb.DocType("Sales Order Item")

	open_so_subquery1 = frappe.qb.from_(bom).select(bom.name).where(bom.is_active == 1)

	open_so_subquery2 = (
		frappe.qb.from_(pi)
		.select(pi.name)
		.where(
			(pi.parent == so.name)
			& (pi.parent_item == so_item.item_code)
			& (
				ExistsCriterion(
					frappe.qb.from_(bom)
					.select(bom.name)
					.where((bom.item == pi.item_code) & (bom.is_active == 1))
				)
			)
		)
	)

	open_so_query = (
		frappe.qb.from_(so)
		.from_(so_item)
		.select(so.name, so.transaction_date, so.customer, so.base_grand_total)
		.distinct()
		.where(
			(so_item.parent == so.name)
			& (so.docstatus == 1)
			& (so.status.notin(["Stopped", "Closed"]))
			& (so.company == self.company)
			& (so_item.qty > so_item.production_plan_qty)
		)
	)

	date_field_mapper = {
		"from_date": so.transaction_date >= self.from_date,
		"to_date": so.transaction_date <= self.to_date,
		"from_delivery_date": so_item.delivery_date >= self.from_delivery_date,
		"to_delivery_date": so_item.delivery_date <= self.to_delivery_date,
	}

	for field, value in date_field_mapper.items():
		if self.get(field):
			open_so_query = open_so_query.where(value)

	for field in ("customer", "project", "sales_order_status"):
		if self.get(field):
			so_field = "status" if field == "sales_order_status" else field
			open_so_query = open_so_query.where(so[so_field] == self.get(field))

	if self.item_code and frappe.db.exists("Item", self.item_code):
		open_so_query = open_so_query.where(so_item.item_code == self.item_code)
		open_so_subquery1 = open_so_subquery1.where(
			self.get_bom_item_condition() or bom.item == so_item.item_code
		)

	open_so_query = open_so_query.where(
		ExistsCriterion(open_so_subquery1) | ExistsCriterion(open_so_subquery2)
	)

	open_so = open_so_query.run(as_dict=True)

	return open_so


@frappe.whitelist()
def get_bin_details(row, company, for_warehouse=None, all_warehouse=False):
	if isinstance(row, str):
		row = frappe._dict(json.loads(row))

	bin = frappe.qb.DocType("Bin")
	wh = frappe.qb.DocType("Warehouse")

	subquery = frappe.qb.from_(wh).select(wh.name).where(wh.company == company)

	warehouse = ""
	if not all_warehouse:
		warehouse = for_warehouse or row.get("source_warehouse") or row.get("default_warehouse")

	if warehouse:
		lft, rgt = frappe.db.get_value("Warehouse", warehouse, ["lft", "rgt"])
		subquery = subquery.where((wh.lft >= lft) & (wh.rgt <= rgt) & (wh.name == bin.warehouse))

	query = (
		frappe.qb.from_(bin)
		.select(
			bin.warehouse,
			IfNull(Sum(bin.projected_qty), 0).as_("projected_qty"),
			IfNull(Sum(bin.actual_qty), 0).as_("actual_qty"),
			IfNull(Sum(bin.ordered_qty), 0).as_("ordered_qty"),
			IfNull(Sum(bin.reserved_qty_for_production), 0).as_("reserved_qty_for_production"),
			IfNull(Sum(bin.planned_qty), 0).as_("planned_qty"),
		)
		.where((bin.item_code == row["item_code"]) & (bin.warehouse.isin(subquery)))
		.groupby(bin.item_code, bin.warehouse)
	)

	return query.run(as_dict=True)


@frappe.whitelist()
def get_so_details(sales_order):
	return frappe.db.get_value(
		"Sales Order", sales_order, ["transaction_date", "customer", "grand_total"], as_dict=1
	)


def get_warehouse_list(warehouses):
	warehouse_list = []

	if isinstance(warehouses, str):
		warehouses = json.loads(warehouses)

	for row in warehouses:
		child_warehouses = frappe.db.get_descendants("Warehouse", row.get("warehouse"))
		if child_warehouses:
			warehouse_list.extend(child_warehouses)
		else:
			warehouse_list.append(row.get("warehouse"))

	return warehouse_list


def get_materials_from_other_locations(item, warehouses, new_mr_items, company):
	from erpnext.stock.doctype.pick_list.pick_list import get_available_item_locations

	stock_uom, purchase_uom = frappe.db.get_value(
		"Item", item.get("item_code"), ["stock_uom", "purchase_uom"]
	)

	locations = get_available_item_locations(
		item.get("item_code"),
		warehouses,
		item.get("quantity") * item.get("conversion_factor"),
		company,
		ignore_validation=True,
	)

	required_qty = item.get("quantity")
	if item.get("conversion_factor") and item.get("purchase_uom") != item.get("stock_uom"):
		# Convert qty to stock UOM
		required_qty = required_qty * item.get("conversion_factor")

	# get available material by transferring to production warehouse
	for d in locations:
		if required_qty <= 0:
			return

		new_dict = copy.deepcopy(item)
		quantity = required_qty if d.get("qty") > required_qty else d.get("qty")

		new_dict.update(
			{
				"quantity": quantity,
				"material_request_type": "Material Transfer",
				"uom": new_dict.get("stock_uom"),  # internal transfer should be in stock UOM
				"from_warehouse": d.get("warehouse"),
				"conversion_factor": 1.0,
			}
		)

		required_qty -= quantity
		new_mr_items.append(new_dict)

	# raise purchase request for remaining qty

	precision = frappe.get_precision("Material Request Plan Item", "quantity")
	if flt(required_qty, precision) > 0:
		required_qty = required_qty

		if frappe.db.get_value("UOM", purchase_uom, "must_be_whole_number"):
			required_qty = ceil(required_qty)

		item["quantity"] = required_qty / item.get("conversion_factor")

		new_mr_items.append(item)


@frappe.whitelist()
def get_item_data(item_code):
	item_details = get_item_details(item_code)

	return {
		"bom_no": item_details.get("bom_no"),
		"stock_uom": item_details.get("stock_uom"),
		# 		"description": item_details.get("description")
	}


@frappe.request_cache
def get_non_completed_production_plans():
	table = frappe.qb.DocType("Production Plan")
	child = frappe.qb.DocType("Production Plan Item")

	return (
		frappe.qb.from_(table)
		.inner_join(child)
		.on(table.name == child.parent)
		.select(table.name)
		.distinct()
		.where(
			(table.docstatus == 1)
			& (table.status.notin(["Completed", "Closed"]))
			& (child.planned_qty > child.ordered_qty)
		)
	).run(pluck="name")


def get_reserved_qty_for_sub_assembly(item_code, warehouse):
	table = frappe.qb.DocType("Production Plan")
	child = frappe.qb.DocType("Production Plan Sub Assembly Item")

	query = (
		frappe.qb.from_(table)
		.inner_join(child)
		.on(table.name == child.parent)
		.select(Sum(child.qty - IfNull(child.wo_produced_qty, 0)))
		.where(
			(table.docstatus == 1)
			& (child.production_item == item_code)
			& (child.fg_warehouse == warehouse)
			& (table.status.notin(["Completed", "Closed"]))
		)
	)

	query = query.run()

	if not query or query[0][0] is None:
		return None

	qty = flt(query[0][0])
	return qty if qty > 0 else 0.0

