let datatable;

const table_columns = [
	{ name: "Invoice No.", id: "name", editable: false, resizable: false },
	{ name: "Date", id: "bill_date", editable: false, resizable: false },
	{ name: "Party", id: "supplier_name", editable: false, resizable: false },
	{ name: "Net Payable", id: "net_total", editable: false, resizable: false },
	{ name: "GST", id: "gst", editable: false, resizable: false },
	{ name: "TDS", id: "apply_tds", editable: false, resizable: false },
	{ name: "Due Date", id: "due_date", editable: false, resizable: false },
];

frappe.pages["outstanding-invoices"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Outstanding Invoices",
		single_column: true,
	});

	let $btn = page.set_secondary_action("Refresh Invoices", () => refresh(), "refresh");
	page.add_action_item("Print", () => open_print_dialog(), true);

	const open_print_dialog = () => {
		frappe.msgprint("Work in progress. Coming soon!");
	};

	const invoice_filters = [
		{
			label: "All",
			fieldname: "all-invoices",
			filterparam: "all",
		},
		{
			label: "30 days old",
			fieldname: "one-month-old",
			filterparam: "30",
		},
		{
			label: "60 days old",
			fieldname: "two-months-old",
			filterparam: "60",
		},
		{
			label: "90 days old",
			fieldname: "three-months-old",
			filterparam: "90",
		},
		{
			label: "120 days old",
			fieldname: "four-months-old",
			filterparam: "120",
		},
		{
			label: "More than 120 days old",
			fieldname: "very-old",
			filterparam: "oldest",
		},
	];

	const filter_buttons = [];

	for (const filter of invoice_filters) {
		var button = page.add_field({
			label: filter.label,
			fieldtype: "Button",
			fieldname: filter.fieldname,
		});

		button.onclick = () => filter_invoices(filter.filterparam);
		filter_buttons.push(button);
	}

	// Add a section to hold the table

	let $table_wrapper = $(`<div style="margin-top: 10px;" id="invoice-table"></div>`);
	page.body.append($table_wrapper);

	const table_data = [];

	// Initialize DataTable
	datatable = new frappe.DataTable($table_wrapper[0], {
		// inlineFilters: true,
		layout: "ratio",
	});

	setTimeout(() => {
		datatable.refresh(table_data, table_columns);
		datatable.setDimensions();
	});

	// function addDynamicRow(sl, name, value) {
	// 	datatable.appendRows([{ sl, name, value }]);
	// }
};

const filter_invoices = () => {
	frappe.call({
		method: "erpnext.administration_dashboard.page.outstanding_invoices.outstanding_invoices.get_invoices",
		callback: function (r) {
			if (r.message) {
				console.log("Server returned message:", r.message);
				datatable.refresh(r.message, table_columns);
			} else {
				frappe.msgprint("Error: Could not get a response from the server.");
			}
		},
	});
};
