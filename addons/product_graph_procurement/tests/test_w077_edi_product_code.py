# -*- coding: utf-8 -*-
"""W077 (R6.6 extension) — vendor part number flows to
product.supplierinfo.product_code so UBL SellersItemIdentification
renders correctly in EDI sends.

Extends the W031 mirror — same ICP gate
(`productgraph.mirror_vendor_part_to_supplierinfo`). The mirror has
always set product_code on create; W077 locks in (a) UPDATE
propagation when vendor_part_number changes after create, and
(b) the non-wiping behaviour when vendor_part_number is cleared
to empty (preserves a previously-set product_code rather than
sending False).
"""
from odoo.tests.common import TransactionCase, tagged

ICP_KEY = 'productgraph.mirror_vendor_part_to_supplierinfo'


@tagged('post_install', '-at_install', 'product_graph', 'w077')
class TestW077EdiProductCode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        cls.product = cls.env['product.product'].create({
            'name': 'W077 Product', 'type': 'consu',
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'W077 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.item.product_id = cls.product.id

        cls.partner = cls.env['res.partner'].create({
            'name': 'W077 Partner', 'company_type': 'company',
        })
        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'W077', 'name': 'W077 Vendor',
            'partner_id': cls.partner.id,
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()
        # Enable the mirror (default off).
        cls.env['ir.config_parameter'].sudo().set_param(ICP_KEY, 'True')

    def _tagged(self, vp):
        return self.env['product.supplierinfo'].sudo().with_context(
            active_test=False,
        ).search([('x_from_pg_vendor_part_id', '=', vp.id)])

    def test_10_product_code_set_on_create(self):
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'EDI-ABC-001',
            'unit_price_hint': 5.0,
            'moq': 1.0,
            'lead_time_days': 7,
        })
        row = self._tagged(vp)
        self.assertEqual(len(row), 1)
        self.assertEqual(
            row.product_code, 'EDI-ABC-001',
            "Mirror must set product_code from vendor_part_number on create.",
        )

    def test_20_product_code_update_propagates(self):
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'EDI-OLD',
            'unit_price_hint': 5.0,
        })
        self.assertEqual(self._tagged(vp).product_code, 'EDI-OLD')
        # Rename → mirror must catch up.
        vp.vendor_part_number = 'EDI-NEW-001'
        row = self._tagged(vp)
        self.assertEqual(
            row.product_code, 'EDI-NEW-001',
            "Updating vendor_part_number must propagate to mirror.",
        )

    def test_30_clearing_vendor_part_number_does_not_wipe_mirror_code(self):
        """W077 hardening — if the source goes empty, leave the mirror
        product_code intact rather than blanking it (otherwise EDI
        sends silently start emitting empty SellersItemIdentification)."""
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'EDI-KEEP-ME',
            'unit_price_hint': 5.0,
        })
        original = self._tagged(vp).product_code
        self.assertEqual(original, 'EDI-KEEP-ME')
        # Source cleared.
        vp.vendor_part_number = False
        row = self._tagged(vp)
        self.assertEqual(
            row.product_code, 'EDI-KEEP-ME',
            "Mirror must NOT blank product_code when source is cleared.",
        )

    def test_40_icp_gate_still_skips_when_off(self):
        # Flip the W031 gate off and confirm a new vendor-part does
        # NOT emit a mirror row (W077 inherits the ICP gate).
        self.env['ir.config_parameter'].sudo().set_param(ICP_KEY, 'False')
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'EDI-OFFGATE',
            'unit_price_hint': 5.0,
        })
        self.assertEqual(
            len(self._tagged(vp)), 0,
            "W077 must remain ICP-gated under the W031 flag.",
        )
        # Restore for cleanliness.
        self.env['ir.config_parameter'].sudo().set_param(ICP_KEY, 'True')

    def test_50_reconciler_repairs_drifted_product_code(self):
        # Mirror creates the row with EDI-RECON-1. Admin manually
        # mutates the mirror row to a wrong code; the reconciler
        # should fix it on next run because pg.vendor.part is the
        # source of truth.
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'EDI-RECON-1',
            'unit_price_hint': 5.0,
        })
        row = self._tagged(vp)
        row.sudo().write({'product_code': 'WRONG-CODE'})
        self.assertEqual(row.product_code, 'WRONG-CODE')
        self.env['pg.vendor.part']._cron_w031_reconcile_supplierinfo()
        row.invalidate_recordset(['product_code'])
        self.assertEqual(
            row.product_code, 'EDI-RECON-1',
            "Reconciler must repair drift on mirror product_code.",
        )
