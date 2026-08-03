// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Slab", {
	refresh(frm) {
		add_custom_badge(frm);
	},
});


function add_custom_badge(frm) {
    // Remove previous custom badge to avoid duplicates
    let label = '';
	let color = '';

	if (frm.doc.is_paused === 1) {
        label = __('Paused');
        color = 'yellow';

        const badge = $(`
            <span class="indicator-pill pause-pill ${color} custom-extra-badge" style="margin-left:8px;">
                ${label}
            </span>
        `);

        frm.page.wrapper.find('.indicator-pill').last().after(badge);
    }
	else {
		frm.page.wrapper.find('.pause-pill').last().remove();
	}

    if (frm.doc.is_cur_stage_complete) {
        label = __('Completed');
        color = 'blue';
        const status_badge = $(`
            <span class="indicator-pill stage-pill ${color} custom-extra-badge" style="margin-left:8px;">
                ${label}
            </span>
        `);

        frm.page.wrapper.find('.indicator-pill').last().before(status_badge);
	}
	else {
		frm.page.wrapper.find('.stage-pill').last().remove();
	}
}
