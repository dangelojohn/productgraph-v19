# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestExpiringQualifications(TransactionCase):
    """PG-133 task 4 — expiring vendor qualifications cron."""

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

    def _make_qualified_vendor(self, code='EXP1', responsible=None):
        v = self.env['pg.vendor'].create({
            'code': code, 'name': f'Vendor {code}',
        })
        if responsible:
            v.responsible_id = responsible
        v.action_to_probationary()
        v.action_qualify()
        return v

    def _add_qualification_with_expiry(self, vendor, days_from_today):
        """Insert a 'qualified' qualification row with a valid_until offset.

        Bypasses the model's write-block by using a fresh create + low-level
        ORM (no inverse writes). The row mirrors what a real qualification
        record looks like after qualify().
        """
        today = fields.Date.context_today(self.env['pg.vendor'])
        valid_until = today + timedelta(days=days_from_today)
        # qualification model blocks write() but not create().
        return self.env['pg.vendor.qualification'].create({
            'vendor_id': vendor.id,
            'decision': 'qualified',
            'decided_by_id': self.env.user.id,
            'valid_from': today,
            'valid_until': valid_until,
        })

    # ──────────────────────────────────────────────────────────────────
    def test_cron_flags_expiring_qualification(self):
        v = self._make_qualified_vendor('NEAR', responsible=self.test_user)
        # Drop a fresh qualification expiring in 15 days.
        qual = self._add_qualification_with_expiry(v, days_from_today=15)
        flagged = self.env['pg.vendor']._cron_flag_expiring_qualifications()
        self.assertGreaterEqual(flagged, 1)
        # Activity raised against the vendor.
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'pg.vendor'),
            ('res_id', '=', v.id),
        ])
        self.assertTrue(activities, 'An activity should be raised.')
        # Chatter message posted.
        msgs = v.message_ids.filtered(lambda m: 'expiring' in (m.body or '').lower())
        self.assertTrue(msgs, 'A chatter message should mention expiring.')
        # is_expired stays False — qual hasn't lapsed yet.
        qual.invalidate_recordset()
        self.assertFalse(qual.is_expired)

    def test_cron_ignores_far_future_qualifications(self):
        v = self._make_qualified_vendor('FAR', responsible=self.test_user)
        self._add_qualification_with_expiry(v, days_from_today=90)
        # Snapshot existing activities (other tests may run before us).
        before = self.env['mail.activity'].search_count([
            ('res_model', '=', 'pg.vendor'),
            ('res_id', '=', v.id),
        ])
        self.env['pg.vendor']._cron_flag_expiring_qualifications()
        after = self.env['mail.activity'].search_count([
            ('res_model', '=', 'pg.vendor'),
            ('res_id', '=', v.id),
        ])
        self.assertEqual(
            before, after,
            'No new activity should be raised for a qualification expiring '
            'outside the 30-day window.',
        )

    def test_cron_is_idempotent(self):
        v = self._make_qualified_vendor('IDEMP', responsible=self.test_user)
        self._add_qualification_with_expiry(v, days_from_today=10)
        self.env['pg.vendor']._cron_flag_expiring_qualifications()
        first_count = self.env['mail.activity'].search_count([
            ('res_model', '=', 'pg.vendor'),
            ('res_id', '=', v.id),
        ])
        self.env['pg.vendor']._cron_flag_expiring_qualifications()
        second_count = self.env['mail.activity'].search_count([
            ('res_model', '=', 'pg.vendor'),
            ('res_id', '=', v.id),
        ])
        self.assertEqual(
            first_count, second_count,
            'A second cron run with no state change must not duplicate the activity.',
        )

    def test_is_expired_flag_flips_when_lapsed(self):
        v = self._make_qualified_vendor('LAPSED', responsible=self.test_user)
        qual = self._add_qualification_with_expiry(v, days_from_today=-5)
        qual.invalidate_recordset()
        self.assertTrue(qual.is_expired)
