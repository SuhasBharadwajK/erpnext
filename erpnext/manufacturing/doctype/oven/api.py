import json

import frappe

from erpnext.manufacturing.doctype.job_card.job_card import JobCard
from erpnext.manufacturing.doctype.operation.api import (
	item_matches_template,
	resolve_job_card_for_slab,
)
from erpnext.manufacturing.doctype.operation.txn_utils import atomic_endpoint
from erpnext.manufacturing.doctype.oven.oven import Oven
from erpnext.manufacturing.doctype.oven_operation.oven_operation import OvenOperation
from erpnext.manufacturing.doctype.oven_rack.oven_rack import OvenRack
from erpnext.manufacturing.doctype.production_line.production_line import get_all_child_lines
from erpnext.manufacturing.doctype.slab.api import get_slabs_for
from erpnext.manufacturing.doctype.slab.slab import Slab
from erpnext.manufacturing.doctype.slab_history.slab_history import SlabHistory
from erpnext.manufacturing.page.operator_station.operator_station import (
	finish_process,
	start_process,
	stop_machine,
)


@frappe.whitelist(allow_guest=True)
def get_oven_from_line(line: str):
	oven_list = frappe.db.get_list("Oven", filters={"line": line})
	if len(oven_list):
		return frappe.get_doc("Oven", oven_list[0].name)

	return None


@frappe.whitelist()
def get_slab_and_job_card_for_oven(process, line="", include_wip=True, slab_template: str | None = None, work_orders: list[str] | None = None):
	if isinstance(include_wip, str):
		include_wip = include_wip.lower() == "true"

	slabs_for_process = get_slabs_for(
		line, process, limit=1000
	)  # Giving an arbitrarily high limit to make sure that the exact number of slabs is fetched.

	if line and not isinstance(line, list):
		child_lines = get_all_child_lines(line)
		if child_lines:
			line = child_lines  # pyright: ignore[reportAssignmentType]

	if not slabs_for_process:
		return {
			"slab": None,
			"available_slabs_count": 0,
			"job_card": None,
			"available_job_cards_count": 0,
		}

	slab = slabs_for_process[0]
	# Resolve the card that belongs to THIS slab (its own production plan and
	# template, never a card bound to another slab). The old top-of-queue query
	# could pair the slab with a same-template card from a different plan.
	slab_doc: Slab = frappe.get_doc("Slab", slab.name)  # pyright: ignore[reportAssignmentType]
	job_card = resolve_job_card_for_slab(
		slab_doc, process, include_wip=include_wip, work_orders=work_orders, line=line
	)

	return {
		"slab": slab,
		"available_slabs_count": len(slabs_for_process),
		"job_card": job_card,
		"available_job_cards_count": 1 if job_card else 0,
	}


@frappe.whitelist()
@atomic_endpoint
def load_slab_into_oven(oven_op: str, line: str, job_card_name: str, slab_template: str):
	oven_operation = json.loads(oven_op)

	new_oven_operation: OvenOperation = frappe.new_doc("Oven Operation")  # pyright: ignore[reportAssignmentType]
	new_oven_operation.update(oven_operation)

	rack_name = new_oven_operation.oven_rack or ""
	slab_name = new_oven_operation.slab or ""
	oven_rack: OvenRack = frappe.get_doc("Oven Rack", rack_name)  # pyright: ignore[reportAssignmentType]

	slab: Slab = frappe.get_doc("Slab", slab_name)  # pyright: ignore[reportAssignmentType]

	# The client-sent card can be stale (fetched earlier, seeded from the URL, or
	# the slab's previous-stage card). Honour it only if it is an open Heating
	# card that produces this slab's template and is not bound to another slab;
	# otherwise resolve the slab's own card server-side.
	if job_card_name:
		jc_info = frappe.db.get_value(
			"Job Card", job_card_name, ["production_item", "slab", "status", "docstatus"], as_dict=True
		)
		is_valid = (
			jc_info
			and jc_info.docstatus == 0
			and jc_info.status in ("Open", "Material Transferred")
			and (not jc_info.slab or jc_info.slab == slab_name)
			and item_matches_template(jc_info.production_item, slab.template)
		)
		if not is_valid:
			job_card_name = ""

	if not job_card_name:
		resolved_card = resolve_job_card_for_slab(slab, "Heating", for_update=True, include_wip=False)
		job_card_name = resolved_card["name"] if resolved_card else ""

	if not job_card_name:
		raise Exception(f"No job card found for slab {slab_name}")

	now_date_time = frappe.utils.now_datetime()  # pyright: ignore

	# Atomicity + deadlock retry are handled by @atomic_endpoint; start_process
	# is reentrant under it and shares this transaction.
	# Start the Job Card
	start_process(job_card_name, slab_name, slab.template, "Heating")
	# Re-fetch: start_process moved the slab and appended to its history.
	slab = frappe.get_doc("Slab", slab_name)  # pyright: ignore[reportAssignmentType]

	new_oven_operation.in_time = now_date_time
	new_oven_operation.job_card = job_card_name
	new_oven_operation.save()

	heating_slab_history_item: SlabHistory = next(h for h in slab.slab_history if h.station == "Heating")

	if heating_slab_history_item.out_time is not None:
		raise Exception("Slab is in an invalid state")

	heating_slab_history_item.oven_params = new_oven_operation.name
	heating_slab_history_item.save()

	oven_rack.current_slab = slab_name
	oven_rack.current_slab_template = slab.template
	oven_rack.start_time = now_date_time
	oven_rack.status = "Heating"
	oven_rack.save()

	oven_rack = frappe.get_doc("Oven Rack", rack_name)  # pyright: ignore[reportAssignmentType]
	return oven_rack


@frappe.whitelist()
@atomic_endpoint
def unload_slab_from_oven(rack_name: str, slab_name: str, slab_template: str, values: str):
	# values is a JSON string containing slab_top_temp, slab_bottom_temp, remarks
	data = json.loads(values)

	# Find active operation for this rack
	op_name = str(
		frappe.db.get_value(
			"Oven Operation",
			{"oven_rack": rack_name, "slab": slab_name, "slab_color": slab_template, "docstatus": 0},
			"name",
		)
	)
	if not op_name:
		frappe.throw("No active operation found for this rack")

	op: OvenOperation = frappe.get_doc("Oven Operation", op_name)  # pyright: ignore[reportAssignmentType]

	now = frappe.utils.now_datetime()  # pyright: ignore
	op.out_time = now
	op.slab_top_temp = data.get("slab_top_temp")
	op.slab_bottom_temp = data.get("slab_bottom_temp")
	op.remarks = data.get("remarks")

	# Calculate total time
	if op.in_time and op.out_time:
		duration = op.out_time - op.in_time  # pyright: ignore
		op.total_time = duration.total_seconds() / 60  # pyright: ignore

	# Reset Rack
	rack: OvenRack = frappe.get_doc("Oven Rack", rack_name)  # pyright: ignore[reportAssignmentType]
	rack.status = "Idle"
	rack.current_slab = None
	rack.current_slab_template = None
	rack.start_time = None

	# Atomicity + deadlock retry are handled by @atomic_endpoint; finish_process
	# is reentrant under it and shares this transaction.
	rack.save()

	op.submit()
	op.save()

	# Complete the Job Card
	if op.job_card:
		finish_process(op.job_card, "Heating", should_stop_machine=False)
		# Check if any of the racks in the oven are in use
		oven: Oven = frappe.get_doc("Oven", rack.parent)  # pyright: ignore[reportAssignmentType]

		# Stop the oven only if all the racks are idle.
		is_in_use = False
		for rack in oven.racks:
			if rack.status == "Heating":
				is_in_use = True
				break

		if not is_in_use:
			stop_machine("Heating", oven.line, None)

		_move_slab_to_cooling_if_enabled(slab_name)

	return {"rack": rack}


def _move_slab_to_cooling_if_enabled(slab_name: str):
	# Check if bypass cooling is enabled in Mahi Granites Settings and return if it is not.
	if not frappe.db.get_single_value("Mahi Granites Settings", "bypass_cooling"):
		return

	method_path = "spl_mods.manufacturing_enhancements.overrides.slab.api.move_slab_iteratively_to"
	method = frappe.get_attr(method_path)
	method(slab_name, "Cooling", use_txn=False, publish_event=True)
