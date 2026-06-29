# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class SlabStateLineMap(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from erpnext.manufacturing.doctype.slab_state_link.slab_state_link import SlabStateLink
		from frappe.types import DF

		line: DF.Link
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		stage_sequence: DF.TableMultiSelect[SlabStateLink]
	# end: auto-generated types
	pass
