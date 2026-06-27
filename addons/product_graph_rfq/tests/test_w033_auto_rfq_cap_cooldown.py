# -*- coding: utf-8 -*-
"""W033 (R6.8) — auto-RFQ batch cap + per-item cooldown.

Two ICP keys (both with sensible defaults) gate the auto-draft cron:

* `southbrook.auto_rfq_max_per_run` (default 10) — caps how many fresh
  RFQs the cron creates in a single pass.
* `southbrook.auto_rfq_cooldown_hours` (default 24) — refuses to
  re-consider the same item within the window.

Both anchor on `pg.item.last_auto_rfq_on`, a Datetime that the cron
stamps for every item it touched (drafted OR posted activity) so the
unsourced-item activity path is also rate-limited.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w033')
class TestW033AutoRfqCap(TransactionCase):

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

    def setUp(self):
        super().setUp()
        # Clean ICPs between tests — each test owns its tuning.
        Param = self.env['ir.config_parameter'].sudo()
        for key in ('southbrook.auto_rfq_max_per_run',
                    'southbrook.auto_rfq_cooldown_hours'):
            existing = Param.search([('key', '=', key)])
            if existing:
                existing.unlink()
        # Seed a category vendor so unsourced items have a candidate
        # the cron can pair them with (otherwise auto_draft bounces
        # them to the activity-only path).
        self.vendor = self.env['pg.vendor'].create({
            'code': 'W33V', 'name': 'W33 Vendor',
        })
        self.vendor.category_ids = [(6, 0, [self.cat.id])]
        self.vendor.action_to_probationary()
        self.vendor.action_qualify()

    def _make_unsourced_items(self, count, prefix='W033'):
        items = self.env['pg.item']
        for i in range(count):
            item = self.env['pg.item'].create({
                'name': f'{prefix}-{i:03d}',
                'item_type': 'purchased',
                'category_id': self.cat.id,
            })
            item.action_to_engineering()
            items |= item
        return items

    def test_10_batch_cap_limits_new_rfqs_per_run(self):
        """5 unsourced items + cap=2 → cron drafts only 2 RFQs in one pass."""
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('southbrook.auto_rfq_max_per_run', '2')
        # Cooldown=0 so test rows aren't double-blocked by the other knob.
        Param.set_param('southbrook.auto_rfq_cooldown_hours', '0')
        items = self._make_unsourced_items(5, prefix='W033CAP')
        result = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        # Filter to OUR items — other tests' fixtures inside the same
        # transaction may legitimately auto-draft too.
        ours = result.filtered(lambda r: r.item_id in items)
        self.assertEqual(
            len(ours), 2,
            f'Cron must respect auto_rfq_max_per_run=2; drafted {len(ours)}.',
        )

    def test_20_cooldown_blocks_repeat_within_window(self):
        """Same item should NOT be redrafted on a follow-up cron run
        while the cooldown stamp is fresh."""
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('southbrook.auto_rfq_cooldown_hours', '24')
        items = self._make_unsourced_items(1, prefix='W033CD')
        first = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        ours_first = first.filtered(lambda r: r.item_id in items)
        self.assertEqual(len(ours_first), 1, 'First run should draft 1 RFQ.')
        # Stamp should be fresh on the item now.
        items.invalidate_recordset()
        self.assertTrue(items.last_auto_rfq_on, 'Cooldown anchor must be set.')
        # Cancel the freshly-drafted RFQ so the open-state dedupe doesn't
        # also short-circuit the run — we want to prove cooldown blocks it.
        ours_first.action_cancel()
        second = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        ours_second = second.filtered(lambda r: r.item_id in items)
        self.assertFalse(
            ours_second,
            'Cron must skip items still inside the cooldown window.',
        )

    def test_30_cooldown_allows_after_window_elapses(self):
        """Backdating last_auto_rfq_on past the cooldown window must
        allow the item to be reconsidered on the next pass."""
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('southbrook.auto_rfq_cooldown_hours', '24')
        items = self._make_unsourced_items(1, prefix='W033CDOUT')
        first = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        ours_first = first.filtered(lambda r: r.item_id in items)
        self.assertEqual(len(ours_first), 1)
        ours_first.action_cancel()
        # Pretend 48h passed — backdate the stamp.
        items.sudo().write({
            'last_auto_rfq_on': fields.Datetime.now() - timedelta(hours=48),
        })
        second = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        ours_second = second.filtered(lambda r: r.item_id in items)
        self.assertEqual(
            len(ours_second), 1,
            'Past-cooldown item must be eligible on the next pass.',
        )

    def test_40_icp_defaults_used_when_unset(self):
        """No ICP set → defaults (10, 24) take effect via the
        accessor helpers."""
        Rfq = self.env['pg.rfq']
        self.assertEqual(Rfq._get_auto_rfq_max_per_run(), 10)
        self.assertEqual(Rfq._get_auto_rfq_cooldown_hours(), 24)

    def test_50_zero_cap_means_unlimited(self):
        """A cap of 0 (or negative) is treated as 'no cap' — useful for
        bulk-import days when an admin wants the legacy behaviour."""
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('southbrook.auto_rfq_max_per_run', '0')
        Param.set_param('southbrook.auto_rfq_cooldown_hours', '0')
        items = self._make_unsourced_items(4, prefix='W033NOCAP')
        result = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        ours = result.filtered(lambda r: r.item_id in items)
        self.assertEqual(
            len(ours), 4,
            'Cap=0 must mean unlimited — all 4 items should auto-draft.',
        )
