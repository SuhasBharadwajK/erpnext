// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

debugger;
frappe.listview_settings["Slab"] = {
	add_fields: ["is_cur_stage_complete"],
	get_indicator(doc) {
		debugger;
		if (doc.is_cur_stage_complete) {
			return [__("Completed"), "blue", "is_cur_stage_complete,=,1"];
		}
	},
	formatters: {
		custom_status(value, df, doc) {
			debugger;
            return `
                <span class="indicator green">Packed</span>
                <span class="indicator blue">Billed</span>
            `;
        }
    }
};
