# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgAuditLog(TransactionCase):

    def test_log_helper_creates_entry(self):
        cat = self.env.ref('product_graph_base.cat_mechanical')
        item = self.env['pg.item'].create({
            'name': 'Audit Test Item',
            'item_type': 'part',
            'category_id': cat.id,
        })
        log = self.env['pg.audit.log']._log(
            item, action='state_change',
            from_state='concept', to_state='prototype',
            notes='unit test',
        )
        self.assertTrue(log.id)
        self.assertEqual(log.res_model, 'pg.item')
        self.assertEqual(log.res_id, item.id)
        self.assertEqual(log.action, 'state_change')

    def test_audit_log_immutable_write(self):
        cat = self.env.ref('product_graph_base.cat_mechanical')
        item = self.env['pg.item'].create({
            'name': 'Audit', 'item_type': 'part', 'category_id': cat.id,
        })
        log = self.env['pg.audit.log']._log(item, action='other', notes='before')
        with self.assertRaises(UserError):
            log.write({'notes': 'after'})

    def test_audit_log_immutable_unlink(self):
        cat = self.env.ref('product_graph_base.cat_mechanical')
        item = self.env['pg.item'].create({
            'name': 'Audit', 'item_type': 'part', 'category_id': cat.id,
        })
        log = self.env['pg.audit.log']._log(item, action='other')
        with self.assertRaises(UserError):
            log.unlink()
