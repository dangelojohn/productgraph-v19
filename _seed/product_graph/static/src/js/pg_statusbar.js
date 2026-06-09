/** @odoo-module **/
/**
 * ProductGraph — Status bar enhancement
 * Odoo 19 OWL 2 compatible
 *
 * Adds a released-item banner to the form view when the record is in
 * 'released' state, making it visually clear the record is immutable.
 */

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

// Add a CSS class to the form root when the pg.item or pg.revision is released.
patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
    },

    get modelName() {
        return this.props.resModel;
    },

    /**
     * Override to inject pg-released banner class.
     */
    get className() {
        const base = super.className || "";
        const pgModels = ["pg.item", "pg.revision"];
        if (
            pgModels.includes(this.modelName) &&
            this.model?.root?.data?.state === "released"
        ) {
            return base + " o_pg_released_banner";
        }
        return base;
    },
});
