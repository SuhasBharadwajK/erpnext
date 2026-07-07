from copy import deepcopy

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.manufacturing.doctype.bom.bom import BOM
from erpnext.manufacturing.doctype.job_card.constants import LOW_PRIORITY
from erpnext.manufacturing.doctype.job_card.job_card import JobCard
from erpnext.manufacturing.doctype.manufacturing_process.constants import MFG_PROCESS_MAP, MIXING_PROCESS
from erpnext.manufacturing.doctype.operation.txn_utils import atomic_endpoint
from erpnext.manufacturing.doctype.slab.slab import Slab
from erpnext.manufacturing.doctype.work_order.work_order import WorkOrder
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry

MAT_TRANS_STOCK_ENTRY_NAMING_SERIES_MAP = {
	"mixing": "MAT-STE-MIXN-TRF-.YYYY.-",
	"distribution": "MAT-STE-DIST-TRF-.YYYY.-",
	"pressing": "MAT-STE-PRES-TRF-.YYYY.-",
	"heating": "MAT-STE-HEAT-TRF-.YYYY.-",
	"cooling": "MAT-STE-COOL-TRF-.YYYY.-",
	"trimming": "MAT-STE-TRIM-TRF-.YYYY.-",
	"calibration": "MAT-STE-CLBR-TRF-.YYYY.-",
	"polishing": "MAT-STE-POLI-TRF-.YYYY.-",
	"quality check": "MAT-STE-QUAL-TRF-.YYYY.-",
}


@frappe.whitelist()
@atomic_endpoint
def transfer_to_next_process(
	current_job_card,
	current_work_order,
	qty=None,
	process=None,
	mixer_number=None,
	work_orders=None,
	line: str | None = None,
):
	"""Transfer FG from Mixing → Next Process Source Warehouse.

	Called directly by the mixer "Transfer" button (an outermost entry point) and
	also nested inside finish_process / the importer. @atomic_endpoint gives the
	mixer path whole-operation deadlock retry; when nested it is a reentrant
	pass-through that simply joins the enclosing transaction.

	``work_orders``, when given (e.g. by the bulk importer against a specific
	production plan), overrides the plan derived from ``current_work_order``:
	the next Work Order for ``process``/this slab is chosen from that explicit
	list instead of ``current_work_order``'s own production_plan, so leftover
	Job Cards on the target plan get used instead of the mixing card's plan.
	"""
	wo: WorkOrder = frappe.get_doc("Work Order", current_work_order)  # pyright: ignore
	fg_item = wo.production_item
	fg_qty = flt(qty or wo.produced_qty)

	process_mapping = deepcopy(MFG_PROCESS_MAP)
	process_mapping["Mixing Operation - SJ"] = process_mapping[
		MIXING_PROCESS
	]  # TODO: Find a better way to do this rather than hardcoding the process name

	current_process = wo.operations[0].operation if wo.operations else ""

	next_process = process_mapping.get(current_process)

	if not next_process:
		frappe.throw(_("No next process found after {0}").format(current_process))

	bom_doc: BOM = frappe.get_doc("BOM", wo.bom_no)  # pyright: ignore

	slab_template = _get_slab_template_from_bom(bom_doc)

	next_wo_filters = {
		"docstatus": ["<", 2],
		"production_item": ["like", f"%{slab_template}%"],
		"production_line": line or wo.production_line,
	}

	if work_orders:
		next_wo_filters["name"] = ["in", work_orders]
	else:
		next_wo_filters["production_plan"] = wo.production_plan

	next_wos = frappe.db.get_list(
		"Work Order",
		filters=next_wo_filters,
		fields=["name"],
		ignore_permissions=True,
	)

	wo_names = [wo.name for wo in next_wos]
	wo_ops = frappe.db.get_list(
		"Work Order Operation",
		filters={
			"parent": ["in", wo_names],
			"operation": ["=", next_process],
		},
		fields=["parent"],
		ignore_permissions=True,
	)

	next_wo = wo_ops[0].parent if wo_ops else None

	if not next_wo:
		fallback_filters = {
			"item_name": ["like", f"%{next_process}%"],
			"docstatus": ["<", 2],
			"production_item": ["like", f"%{slab_template}%"],
		}
		if work_orders:
			fallback_filters["name"] = ["in", work_orders]
		else:
			fallback_filters["production_plan"] = wo.production_plan

		next_wo = frappe.db.get_value("Work Order", fallback_filters, "name")

	if not next_wo:
		frappe.throw(f"Next WO for '{next_process}' not found.")

	next_wo_doc = frappe.get_doc("Work Order", next_wo)

	# The slab being carried forward is the one bound to the current job card.
	# (Empty for Mixing -> Distribution, where the slab does not exist yet.)
	slab_no = frappe.db.get_value("Job Card", current_job_card, "slab")
	open_job_card = _select_open_job_card_for_next_wo(next_wo, slab_no)

	if not open_job_card:
		frappe.throw(f"No open job cards available")
	bom_doc = frappe.get_doc("BOM", next_wo_doc.bom_no)

	transfer_qty = 0
	for bom_item in bom_doc.items:
		if bom_item.item_code == fg_item:
			transfer_qty = flt(bom_item.stock_qty)
			break

	_set_job_card_completion_status(current_job_card, transfer_qty, fg_qty)

	if transfer_qty == 0:
		frappe.throw(f"BOM qty for {fg_item} not found in {next_wo} BOM")

	job_card_item = frappe.db.get_value(
		"Job Card Item", {"parent": open_job_card, "item_code": fg_item, "parenttype": "Job Card"}, "name"
	)

	if not job_card_item:
		frappe.throw(f"No Job Card Item found for {fg_item} in {open_job_card}")

	se = create_material_transfer_stock_entry(
		next_wo=next_wo,
		open_job_card=open_job_card,
		company=wo.company,
		fg_item=fg_item,
		transfer_qty=transfer_qty,
		current_job_card=current_job_card,
		stock_uom=wo.stock_uom,
		s_warehouse=wo.fg_warehouse,
		t_warehouse=next_wo_doc.wip_warehouse,
		job_card_item=job_card_item,
		next_station=next_process or "",
	)

	job_card_item_doc = frappe.get_doc("Job Card Item", job_card_item)
	job_card_item_doc.transferred_qty = transfer_qty
	job_card_item_doc.save(ignore_permissions=True)

	open_jc_doc = frappe.get_doc("Job Card", open_job_card)
	open_jc_doc.transferred_qty = sum(item.transferred_qty for item in open_jc_doc.items)
	if mixer_number:
		open_jc_doc.mixer_number = mixer_number

	# Bind the card to the slab so a concurrent transfer/station cannot claim it.
	if slab_no and not open_jc_doc.slab:
		open_jc_doc.slab = slab_no
		open_jc_doc.slab_template = frappe.db.get_value("Slab", slab_no, "template")

	open_jc_doc.save(ignore_permissions=True)

	if process == "Mixing":
		frappe.publish_realtime("refresh_operator_station")

	return {
		"status": "Success",
		"transfer_se": se.name,
		"next_work_order": next_wo,
		"job_card": open_job_card,
		"job_card_item": job_card_item,
		"qty_transferred": transfer_qty,
		"from_warehouse": wo.fg_warehouse,
		"to_warehouse": next_wo_doc.wip_warehouse,
		"transferred_qty_updated": job_card_item_doc.transferred_qty,  # ✅ New!
		"header_transferred_qty": open_jc_doc.transferred_qty,
		"message": f"Transferred {fg_qty} {fg_item} to {next_wo}",
		"mixer_number": mixer_number,
	}


@frappe.whitelist()
def get_recent_job_card(operation, production_line=None):
	if operation == "Mixing":
		filters = {
			"status": ["in", ["Open", "Material Transferred", "Work In Progress", "Completed"]],
			"docstatus": [">=", 0],
			"operation": ["like", "%Mixing%"],
		}
	else:
		filters = {
			"status": ["in", ["Material Transferred", "Work In Progress"]],
			"docstatus": 0,
			"operation": ["like", f"%{operation}%"],
		}

	if production_line:
		filters["production_line"] = production_line

	job_cards = frappe.db.get_list(
		"Job Card",
		filters=filters,
		fields=["name", "operation", "status", "work_order"],
		order_by="creation asc",
	)

	if len(job_cards) == 0:
		frappe.throw(_("No job cards found for operation {0}").format(operation))
	return job_cards[0]


@frappe.whitelist()
def get_open_job_cards(
	process,
	line=None,
	include_wip=True,
	include_material_transferred=True,
	include_paused=True,
	item_code=None,
	slab_template="",
	limit=0,
	exclude_job_cards="",
	work_orders:list[str] | None=None,
	slab=None,
	production_plan=None,
):
	is_mixing = process == "Mixing"
	if is_mixing:
		filters = {
			"status": ["in", ["Open", "Material Transferred", "Work In Progress", "Completed"]],
			"docstatus": [">=", 0],
			"operation": ["like", "%Mixing%"],
			"is_finished": ["=", "0"],
		}
	else:
		workstation_names = [x.workstation_name for x in _get_workstations(process)]

		if workstation_names:
			ws_query = ["in", workstation_names]
		else:
			ws_query = ["like", f"%{process}%"]

		in_query = []

		if include_material_transferred:
			in_query.append("Material Transferred")

		if include_wip:
			in_query.append("Work In Progress")

		if include_paused:
			in_query.append("On Hold")

		filters = {
			"status": ["in", in_query],
			"docstatus": ["=", "0"],
			"workstation": ws_query,
		}

	# Scope to a production plan by resolving its work orders (Job Card has no
	# direct production_plan field).
	if production_plan and not work_orders:
		work_orders = frappe.get_all(
			"Work Order",
			filters={"production_plan": production_plan, "docstatus": ["<", 2]},
			pluck="name",
			ignore_permissions=True,
		) or ["__none__"]

	if work_orders:
		filters["work_order"] = ["in", work_orders]

	if slab:
		filters["slab"] = slab

	if slab_template:
		filters["production_item"] = ["like", f"{slab_template} - %"]

	if exclude_job_cards:
		if isinstance(exclude_job_cards, list):
			filters["name"] = ["not", "in", exclude_job_cards]
		else:
			filters["name"] = ["!=", exclude_job_cards]

	if line:
		if isinstance(line, list):
			filters["production_line"] = ["in", line]
		else:
			filters["production_line"] = line

	if item_code:
		filters["production_item"] = ["like", f"%{item_code}%"]

	limit = limit or (
		9999999
		if not is_mixing
		or frappe.get_single_value("Mahi Granites Settings", "show_job_card_queue_to_mixer_operators")
		else 1
	)

	job_cards = frappe.get_all(
		"Job Card",
		limit=limit,
		filters=filters,
		fields=[
			"name",
			"work_order",
			"status",
			"production_item",
			"slab",
			"slab_template",
			"workstation",
			"workstation_type",
			"started_time",
			"creation",
			"modified",
			"production_line",
		],
		order_by="priority asc, status asc, creation asc",
		ignore_permissions=True,
	)

	return job_cards


def _get_workstations(workstation_type: str):
	return frappe.get_all(
		"Workstation",
		filters={"workstation_type": ["like", f"%{workstation_type}%"]},
		fields=["workstation_name"],
	)


def _select_open_job_card_for_next_wo(next_wo: str, slab_no: str | None):
	"""Pick the open Job Card on ``next_wo`` to receive the transferred material.

	When the slab is known, prefer the card already bound to that slab; failing
	that, claim the earliest *unbound* card. The selection is locked
	``for_update`` so two concurrent transfers cannot grab the same card.
	When there is no slab yet (Mixing -> Distribution), fall back to the
	earliest open card.
	"""
	base = {"work_order": next_wo, "status": "Open", "docstatus": 0}

	if slab_no:
		bound = frappe.db.get_value(
			"Job Card", {**base, "slab": slab_no}, "name", order_by="creation asc", for_update=True
		)
		if bound:
			return bound
		return frappe.db.get_value(
			"Job Card",
			{**base, "slab": ["is", "not set"]},
			"name",
			order_by="creation asc",
			for_update=True,
		)

	return frappe.db.get_value("Job Card", base, "name", order_by="creation asc", for_update=True)


def _get_slab_production_plan(slab) -> str | None:
	"""Derive the production plan that owns ``slab`` via its job-card chain."""
	jc_name = slab.current_job_card
	if not jc_name:
		for history in reversed(slab.slab_history or []):
			if history.job_card_number:
				jc_name = history.job_card_number
				break

	if not jc_name:
		return None

	work_order = frappe.db.get_value("Job Card", jc_name, "work_order")
	if not work_order:
		return None

	return frappe.db.get_value("Work Order", work_order, "production_plan")


def resolve_job_card_for_slab(
	slab: Slab,
	process: str,
	*,
	for_update: bool = False,
	include_wip: bool = True,
	include_paused: bool = False,
	work_orders: list[str] | None = None,
	line: str | None = None,
):
	"""Authoritative, slab-aware resolver for the next Job Card of a slab.

	Scopes candidates to the slab's own production plan, matching production
	item (template) and line, then prefers the card already bound to the slab
	and otherwise the earliest unbound card. Never returns a card bound to a
	*different* slab. When ``for_update`` is set, the chosen card is locked so a
	concurrent station cannot claim it; callers should immediately bind it
	(``jc.slab = slab.name``).

	``line``, when given (e.g. the bulk importer's explicit Production Line),
	overrides ``slab.child_line`` for the line filter below.
	"""
	if isinstance(slab, str):
		slab = frappe.get_doc("Slab", slab)

	# An explicit work-order list (e.g. from the bulk importer) takes precedence;
	# otherwise scope to the slab's own production plan.
	production_plan = None if work_orders else _get_slab_production_plan(slab)

	candidates = get_open_job_cards(
		process,
		line=line or slab.child_line,
		include_wip=include_wip,
		include_material_transferred=True,
		include_paused=include_paused,
		item_code=slab.template,
		production_plan=production_plan,
		work_orders=work_orders,
	)

	bound = next((c for c in candidates if c.get("slab") == slab.name), None)
	chosen = bound or next((c for c in candidates if not c.get("slab")), None)
	if not chosen:
		return None

	if for_update:
		# Claim the row so a concurrent station cannot grab the same card.
		frappe.db.get_value("Job Card", chosen["name"], "name", for_update=True)

	return chosen


@frappe.whitelist()
def get_operators(designation, production_line):
	filters = {
		"designation": designation,
		"production_line": production_line,
	}

	employee_name = frappe.db.get_value("Employee", filters, "name")

	if not employee_name:
		frappe.throw(f"No operator found: designation={designation}, line={production_line}")

	return employee_name


def _get_slab_template_from_bom(bom_doc):
	# template_components = bom_doc.slab_template.split("-") if bom_doc.slab_template else []
	# size_index = 2  # TODO: This depends on the template's naming structure. Use a reliable way to do it like fetching the slab template and then the size from within it.
	# for index, _ in enumerate(template_components):
	# 	if index == size_index:
	# 		temp = re.sub(r"0", "00", template_components[index])
	# 		template_components[index] = re.sub(r"00", "CM", temp)
	# slab_template = "-".join(template_components)
	return bom_doc.slab_template


def _set_job_card_completion_status(jc_name: str, bom_qty: float, fg_qty: float):
	jc: JobCard = frappe.get_doc("Job Card", jc_name)  # pyright: ignore
	prepared_qty = (fg_qty if fg_qty else jc.total_completed_qty) or 0  # pyright: ignore

	display_qty = flt(prepared_qty - bom_qty, 3)
	bom_qty = flt(bom_qty, 2)
	is_job_card_finished = display_qty < bom_qty and jc.status == "Completed"

	if is_job_card_finished:
		jc.is_finished = 1
		jc.priority = LOW_PRIORITY
		jc.save(ignore_permissions=True)
		jc.reload()


def create_material_transfer_stock_entry(
	next_wo: str,
	open_job_card: str,
	company: str,
	fg_item: str,
	transfer_qty: float,
	current_job_card: str,
	stock_uom: str,
	s_warehouse: str,
	t_warehouse: str,
	job_card_item: str,
	next_station: str,
):
	stock_entry: StockEntry = frappe.new_doc("Stock Entry")  # pyright: ignore
	stock_entry.purpose = "Material Transfer for Manufacture"
	stock_entry.work_order = next_wo  # pyright: ignore
	stock_entry.job_card = open_job_card  # pyright: ignore # No job card for inter-process transfer
	stock_entry.company = company
	stock_entry.fg_completed_qty = transfer_qty
	stock_entry.previous_job_card = current_job_card

	stock_entry.append(
		"items",
		{
			"item_code": fg_item,
			"qty": transfer_qty,
			"stock_uom": stock_uom,
			"uom": stock_uom,
			"conversion_factor": 1.0,
			"s_warehouse": s_warehouse,
			"t_warehouse": t_warehouse,
			"basic_rate": 0,
			"job_card_item": job_card_item,
		},
	)

	stock_entry.naming_series = MAT_TRANS_STOCK_ENTRY_NAMING_SERIES_MAP.get(next_station.lower(), "MAT-STE-.YYYY.-")  # pyright: ignore[reportAttributeAccessIssue]
	stock_entry.set_stock_entry_type()
	stock_entry.set_missing_values()

	# Deadlocks are handled at the endpoint level by run_atomic(), which rolls
	# back and retries the whole operation. A fragment-level retry here would
	# leave the earlier writes (job card / work order) committed-in-progress.
	stock_entry.insert()
	stock_entry.submit()

	return stock_entry


@frappe.whitelist()
def get_job_card_for_operation(operation: str, slab_number: str | None = None):
	filters = {"operation": operation, "status": "Open", "docstatus": 0}
	if slab_number:
		filters["slab"] = slab_number

	open_job_card: str = frappe.db.get_value(  # pyright: ignore[reportAssignmentType]
		"Job Card",
		filters,
		"name",
		order_by="creation desc",
	)

	return open_job_card
