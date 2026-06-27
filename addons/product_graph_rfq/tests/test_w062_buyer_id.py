# -*- coding: utf-8 -*-
"""W062 (R6.16) — buyer_id on pg.rfq + stored related on pg.rfq.line.

Multi-buyer org needs a "RFQs I own" filter that's distinct from
"RFQs I requested as an engineer". buyer_id default = current user;
search filter `filter_my_buyer` domain = [('buyer_id', '=', uid)].

Locks in:
  * buyer_id defaults to env.user on create.
  * Reassigning buyer_id writes through tracking (visible in chatter).
  * pg.rfq.line.buyer_id mirrors the header (stored related).
  * Search domain `[('buyer_id', '=', uid)]` returns only this user's
    RFQs, even when another user is the requestor.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w062')
class TestW062BuyerId(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        cls.admin.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.admin)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        # Second user for the multi-buyer scoping test.
        cls.buyer_user = cls.env['res.users'].create({
            'name': 'W062 Buyer Two',
            'login': 'w062_buyer2',
            'email': 'w062_b2@example.com',
            'group_ids': [(6, 0, [
                cls.env.ref('product_graph_base.group_pg_engineer').id,
            ])],
        })

        cls.item = cls.env['pg.item'].create({
            'name': 'W062 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

    def test_10_buyer_id_defaults_to_env_user(self):
        rfq = self.env['pg.rfq'].create({'title': 'W062 default-buyer'})
        self.assertEqual(rfq.buyer_id, self.admin)

    def test_20_buyer_id_distinct_from_requestor(self):
        # Admin requests, buyer_user owns.
        rfq = self.env['pg.rfq'].create({
            'title': 'W062 split-ownership',
            'buyer_id': self.buyer_user.id,
        })
        self.assertEqual(rfq.requestor_id, self.admin)
        self.assertEqual(rfq.buyer_id, self.buyer_user)

    def test_30_line_buyer_id_mirrors_header(self):
        rfq = self.env['pg.rfq'].create({
            'title': 'W062 mirror',
            'buyer_id': self.buyer_user.id,
        })
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 1.0,
        })
        self.assertEqual(line.buyer_id, self.buyer_user)
        # Reassign on header → line follows (stored related).
        rfq.buyer_id = self.admin
        line.invalidate_recordset(['buyer_id'])
        self.assertEqual(line.buyer_id, self.admin)

    def test_40_search_filter_scopes_to_buyer(self):
        # Two RFQs — one owned by admin, one by buyer_user.
        rfq_admin = self.env['pg.rfq'].create({'title': 'W062 admin-owned'})
        rfq_other = self.env['pg.rfq'].create({
            'title': 'W062 other-owned',
            'buyer_id': self.buyer_user.id,
        })
        # As admin, "buyer_id = my uid" must return only admin's RFQ.
        mine = self.env['pg.rfq'].search([
            ('buyer_id', '=', self.admin.id),
            ('id', 'in', (rfq_admin + rfq_other).ids),
        ])
        self.assertIn(rfq_admin, mine)
        self.assertNotIn(rfq_other, mine)

    def test_50_buyer_id_indexed_for_fast_search(self):
        # Sanity: the field must declare index=True for the search
        # filter to scale on a large RFQ table.
        field = self.env['pg.rfq']._fields['buyer_id']
        self.assertTrue(
            field.index,
            "pg.rfq.buyer_id should be indexed for the My-RFQs filter.",
        )
        line_field = self.env['pg.rfq.line']._fields['buyer_id']
        self.assertTrue(line_field.index)
        self.assertTrue(line_field.store)
