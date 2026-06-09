# -*- coding: utf-8 -*-
"""Tests for pg.audit.log — immutability contract."""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPgAuditLog(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['pg.category'].create({
            'name': 'Audit Test Cat', 'code': 'AUD-TC',
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'Audit Test Item',
            'item_type': 'manufactured',
            'category_id': cls.category.id,
        })

    def _make_log(self):
        return self.env['pg.audit.log'].sudo().create({
            'item_id': self.item.id,
            'action': 'state_change',
            'from_state': 'draft',
            'to_state': 'engineering',
            'user_id': self.env.uid,
            'notes': 'Test log entry',
        })

    def test_log_entry_created(self):
        log = self._make_log()
        self.assertTrue(log.id)
        self.assertEqual(log.action, 'state_change')

    def test_log_write_raises(self):
        log = self._make_log()
        with self.assertRaises(UserError):
            log.write({'notes': 'Tampered'})

    def test_log_unlink_raises(self):
        log = self._make_log()
        with self.assertRaises(UserError):
            log.unlink()

    def test_log_name_computed(self):
        log = self._make_log()
        self.assertIn(self.item.item_number, log.name)
        self.assertIn('state_change', log.name)
