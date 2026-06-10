# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgVendorAvl(TransactionCase):

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

    def _make_item(self):
        item = self.env['pg.item'].create({
            'name': 'AVL Test Item',
            'item_type': 'purchased',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _make_vendor(self, code, qualified=False):
        v = self.env['pg.vendor'].create({'code': code, 'name': f'Vendor {code}'})
        if qualified:
            v.action_to_probationary()
            v.action_qualify()
        return v

    def _make_part(self, item, vendor, vpn='VPN-1', rank=10, preferred=False):
        return self.env['pg.vendor.part'].create({
            'item_id': item.id,
            'vendor_id': vendor.id,
            'vendor_part_number': vpn,
            'rank': rank,
            'is_preferred': preferred,
        })

    # ──────────────────────────────────────────────────────────────────
    # Primary-source resolution
    # ──────────────────────────────────────────────────────────────────
    def test_primary_is_preferred_when_set(self):
        item = self._make_item()
        v1 = self._make_vendor('V1', qualified=True)
        v2 = self._make_vendor('V2', qualified=True)
        self._make_part(item, v1, 'A', rank=5)
        part2 = self._make_part(item, v2, 'B', rank=20, preferred=True)
        item.invalidate_recordset()
        self.assertEqual(item.primary_vendor_part_id, part2)
        self.assertEqual(item.primary_vendor_id, v2)

    def test_primary_falls_back_to_lowest_rank_qualified(self):
        item = self._make_item()
        v_qualified_low = self._make_vendor('V1', qualified=True)
        v_unqualified = self._make_vendor('V2', qualified=False)
        part_low = self._make_part(item, v_qualified_low, 'A', rank=5)
        # Even though rank=1 is lower, vendor isn't qualified — should be skipped.
        self._make_part(item, v_unqualified, 'B', rank=1)
        item.invalidate_recordset()
        self.assertEqual(item.primary_vendor_part_id, part_low)

    def test_primary_is_empty_when_no_qualified_source(self):
        item = self._make_item()
        v = self._make_vendor('V1', qualified=False)
        self._make_part(item, v, 'A')
        item.invalidate_recordset()
        self.assertFalse(item.primary_vendor_part_id)

    def test_primary_updates_when_vendor_qualifies(self):
        item = self._make_item()
        v = self._make_vendor('V1', qualified=False)
        part = self._make_part(item, v, 'A')
        item.invalidate_recordset()
        self.assertFalse(item.primary_vendor_part_id)
        v.action_to_probationary()
        v.action_qualify()
        item.invalidate_recordset()
        self.assertEqual(item.primary_vendor_part_id, part)

    # ──────────────────────────────────────────────────────────────────
    # One-preferred-per-item constraint
    # ──────────────────────────────────────────────────────────────────
    def test_only_one_preferred_source_per_item(self):
        item = self._make_item()
        v1 = self._make_vendor('V1', qualified=True)
        v2 = self._make_vendor('V2', qualified=True)
        self._make_part(item, v1, 'A', preferred=True)
        with self.assertRaises(ValidationError):
            self._make_part(item, v2, 'B', preferred=True)

    # ──────────────────────────────────────────────────────────────────
    # Vendor-part display name
    # ──────────────────────────────────────────────────────────────────
    def test_vendor_part_display_name(self):
        item = self._make_item()
        v = self._make_vendor('V1', qualified=True)
        p = self._make_part(item, v, 'XYZ-123')
        self.assertIn('Vendor V1', p.display_name)
        self.assertIn('XYZ-123', p.display_name)

    # ──────────────────────────────────────────────────────────────────
    # vendor_part_count
    # ──────────────────────────────────────────────────────────────────
    def test_vendor_part_count(self):
        item = self._make_item()
        v1 = self._make_vendor('V1', qualified=True)
        v2 = self._make_vendor('V2', qualified=True)
        self._make_part(item, v1, 'A')
        self._make_part(item, v2, 'B')
        item.invalidate_recordset()
        self.assertEqual(item.vendor_part_count, 2)
