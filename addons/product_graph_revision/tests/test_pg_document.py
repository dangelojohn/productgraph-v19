# -*- coding: utf-8 -*-
import base64
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgDocument(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
        )

    def _make_revision(self):
        cat = self.env.ref('product_graph_base.cat_mechanical')
        item = self.env['pg.item'].create({
            'name': 'Doc Test Item',
            'item_type': 'assembly',
            'category_id': cat.id,
        })
        item.action_to_engineering()
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release for document tests.',
        })

    def test_upload_document(self):
        rev = self._make_revision()
        doc = self.env['pg.document'].create({
            'name': 'Cabinet Drawing',
            'revision_id': rev.id,
            'document_type': 'drawing',
            'version_label': 'v1.0',
            'file_data': base64.b64encode(b'fake-pdf-bytes'),
            'file_name': 'cab_drawing.pdf',
        })
        self.assertTrue(doc.id)
        self.assertEqual(rev.document_count, 1)

    def test_document_locked_on_released_revision(self):
        rev = self._make_revision()
        doc = self.env['pg.document'].create({
            'name': 'Cabinet Drawing',
            'revision_id': rev.id,
            'document_type': 'drawing',
            'file_data': base64.b64encode(b'data'),
            'file_name': 'd.pdf',
        })
        rev.action_submit_for_review()
        rev.action_release()
        with self.assertRaises(UserError):
            doc.write({'name': 'Renamed'})
        with self.assertRaises(UserError):
            doc.unlink()
