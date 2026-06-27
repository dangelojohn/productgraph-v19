# -*- coding: utf-8 -*-
"""W031 (R6.6) — pg.vendor.part -> product.supplierinfo mirror tests.

Locks in:
  * ICP-gated (mirror disabled by default -> no supplierinfo rows
    appear on vendor-part create).
  * When ICP enabled, creating an eligible vendor-part creates a tagged
    product.supplierinfo row.
  * Update propagates (price, MOQ, lead-time) on subsequent write.
  * Manual / untagged supplierinfo rows are NEVER overwritten by the
    mirror or the reconciler cron.
  * Vendor de-qualification archives the tagged mirror row but does
    not affect untagged rows.
  * Reconciler cron is idempotent.
"""
from odoo.tests.common import TransactionCase, tagged

ICP_KEY = 'productgraph.mirror_vendor_part_to_supplierinfo'


@tagged('post_install', '-at_install', 'product_graph', 'w031')
class TestW031SupplierinfoMirror(TransactionCase):

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
            'name': 'W031 Product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'W031 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.item.product_id = cls.product.id

        cls.partner = cls.env['res.partner'].create({
            'name': 'W031 Vendor Partner', 'company_type': 'company',
        })
        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'W031-V', 'name': 'W031 Vendor',
            'partner_id': cls.partner.id,
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _set_icp(self, enabled):
        self.env['ir.config_parameter'].sudo().set_param(
            ICP_KEY, 'True' if enabled else 'False',
        )

    def _tagged_rows(self, vp):
        return self.env['product.supplierinfo'].sudo().with_context(
            active_test=False,
        ).search([('x_from_pg_vendor_part_id', '=', vp.id)])

    # ──────────────────────────────────────────────────────────────────
    # ICP gate (default off)
    # ──────────────────────────────────────────────────────────────────
    def test_10_icp_off_no_mirror_on_create(self):
        """With ICP off (default), creating a vendor-part does NOT
        emit a product.supplierinfo row."""
        self._set_icp(False)
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'VPN-X',
            'unit_price_hint': 5.00,
            'moq': 10.0,
            'lead_time_days': 7,
        })
        self.assertEqual(
            len(self._tagged_rows(vp)), 0,
            "ICP off must skip the mirror on create.",
        )

    # ──────────────────────────────────────────────────────────────────
    # ICP on — happy path
    # ──────────────────────────────────────────────────────────────────
    def test_20_icp_on_creates_tagged_mirror(self):
        self._set_icp(True)
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'VPN-Y',
            'unit_price_hint': 4.25,
            'moq': 50.0,
            'lead_time_days': 21,
        })
        rows = self._tagged_rows(vp)
        self.assertEqual(
            len(rows), 1,
            "Eligible vendor-part should produce exactly 1 tagged "
            "supplierinfo row.",
        )
        self.assertEqual(rows.partner_id, self.partner)
        self.assertEqual(rows.product_tmpl_id, self.product.product_tmpl_id)
        self.assertEqual(rows.price, 4.25)
        self.assertEqual(rows.min_qty, 50.0)
        self.assertEqual(rows.delay, 21)
        self.assertEqual(rows.product_code, 'VPN-Y')

    def test_30_write_propagates_to_tagged_mirror(self):
        self._set_icp(True)
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'VPN-Z',
            'unit_price_hint': 4.25,
            'moq': 50.0,
            'lead_time_days': 21,
        })
        vp.write({
            'unit_price_hint': 3.99,
            'lead_time_days': 30,
        })
        rows = self._tagged_rows(vp)
        self.assertEqual(rows.price, 3.99)
        self.assertEqual(rows.delay, 30)

    # ──────────────────────────────────────────────────────────────────
    # Untagged (admin) rows preserved
    # ──────────────────────────────────────────────────────────────────
    def test_40_untagged_rows_never_overwritten(self):
        """An admin-created supplierinfo row (no x_from_pg_vendor_part_id)
        survives mirror create + reconciler cron untouched."""
        self._set_icp(True)
        # Admin pre-existing row.
        admin_row = self.env['product.supplierinfo'].sudo().create({
            'partner_id': self.partner.id,
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'price': 99.99,
            'min_qty': 1.0,
            'delay': 1,
            'product_code': 'ADMIN-OVERRIDE',
        })
        self.assertFalse(admin_row.x_from_pg_vendor_part_id)
        # Mirror create.
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'VPN-MIRROR',
            'unit_price_hint': 4.25,
            'moq': 50.0,
            'lead_time_days': 21,
        })
        # Run the reconciler too.
        self.env['pg.vendor.part']._cron_w031_reconcile_supplierinfo()
        # Admin row untouched.
        admin_row.invalidate_recordset(['price', 'product_code', 'min_qty'])
        self.assertEqual(admin_row.price, 99.99)
        self.assertEqual(admin_row.product_code, 'ADMIN-OVERRIDE')
        self.assertEqual(admin_row.min_qty, 1.0)
        # Mirror row exists alongside.
        self.assertEqual(len(self._tagged_rows(vp)), 1)

    # ──────────────────────────────────────────────────────────────────
    # De-qualification archives mirror row
    # ──────────────────────────────────────────────────────────────────
    def test_50_disqualification_archives_mirror(self):
        self._set_icp(True)
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'VPN-DQ',
            'unit_price_hint': 4.25,
            'moq': 50.0,
            'lead_time_days': 21,
        })
        row = self._tagged_rows(vp)
        self.assertEqual(len(row), 1)
        self.assertTrue(row.active)
        # Disqualify the vendor + run reconciler.
        self.vendor.with_context(
            default_qualification_comment='Test disqualification',
        ).action_disqualify()
        self.env['pg.vendor.part']._cron_w031_reconcile_supplierinfo()
        row.invalidate_recordset(['active'])
        self.assertFalse(
            row.active,
            "Mirror row should be archived once the vendor is disqualified.",
        )

    # ──────────────────────────────────────────────────────────────────
    # Reconciler idempotency
    # ──────────────────────────────────────────────────────────────────
    def test_60_reconciler_idempotent(self):
        self._set_icp(True)
        vp = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor.id,
            'vendor_part_number': 'VPN-IDEM',
            'unit_price_hint': 4.25,
            'moq': 50.0,
            'lead_time_days': 21,
        })
        # First run.
        self.env['pg.vendor.part']._cron_w031_reconcile_supplierinfo()
        first_write = self._tagged_rows(vp).write_date
        # Second run, no source change → no write.
        self.env['pg.vendor.part']._cron_w031_reconcile_supplierinfo()
        second_write = self._tagged_rows(vp).write_date
        self.assertEqual(
            first_write, second_write,
            "Reconciler must NOT bump write_date when nothing changed.",
        )
