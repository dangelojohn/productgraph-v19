# -*- coding: utf-8 -*-
"""PG-125 — res.config.settings panel ICP round-trip.

Each Boolean field on the settings form is bound to an ICP key via
`config_parameter=`. We assert the round trip: set on the form → execute()
writes the ICP → fresh read yields the same value.

Odoo encodes Booleans in ICP as the strings 'True' / 'False'.
"""
from odoo.tests.common import TransactionCase, tagged


PG_ICP_KEYS = {
    'pg_enforce_sod': 'productgraph.enforce_sod',
    'pg_auto_advance_item_on_revision_release':
        'productgraph.auto_advance_item_on_revision_release',
    'pg_auto_close_eco_on_all_revisions_released':
        'productgraph.auto_close_eco_on_all_revisions_released',
    'pg_auto_spawn_revision_from_eco_line':
        'productgraph.auto_spawn_revision_from_eco_line',
    'pg_notify_on_auto_advance': 'productgraph.notify_on_auto_advance',
    'pg_eco_impact_explode_enabled':
        'productgraph.eco_impact_explode_enabled',
    'pg_eco_route_on_submit': 'productgraph.eco_route_on_submit',
    'pg_eco_sla_enabled': 'productgraph.eco_sla_enabled',
}


@tagged('post_install', '-at_install', 'product_graph')
class TestResConfigSettingsPg(TransactionCase):

    def _icp(self):
        return self.env['ir.config_parameter'].sudo()

    def test_sod_setting_writes_to_icp(self):
        """Setting pg_enforce_sod=False on the form must land 'False' in
        the productgraph.enforce_sod ICP key."""
        settings = self.env['res.config.settings'].create({
            'pg_enforce_sod': False,
        })
        settings.execute()
        # get_param returns either '' / False / 'False' for a falsy ICP in v19.
        val = self._icp().get_param('productgraph.enforce_sod')
        self.assertIn(val, (False, '', 'False', 'false'),
                      f"Expected falsy ICP, got {val!r}")

        # Flip back to True so we know the inverse direction also works.
        settings2 = self.env['res.config.settings'].create({
            'pg_enforce_sod': True,
        })
        settings2.execute()
        val = self._icp().get_param('productgraph.enforce_sod')
        self.assertIn(val, (True, 'True', 'true', '1'),
                      f"Expected truthy ICP, got {val!r}")

    def test_all_icps_round_trip(self):
        """Every PG settings field must round-trip its Boolean through ICP."""
        # First write False for every field, save, then assert every ICP
        # is 'False'.
        all_false = {field_name: False for field_name in PG_ICP_KEYS}
        settings = self.env['res.config.settings'].create(all_false)
        settings.execute()
        for key in PG_ICP_KEYS.values():
            val = self._icp().get_param(key)
            self.assertIn(val, (False, '', 'False', 'false'),
                          f"ICP key {key} did not round-trip to falsy; got {val!r}")

        # Now flip every field to True.
        all_true = {field_name: True for field_name in PG_ICP_KEYS}
        settings = self.env['res.config.settings'].create(all_true)
        settings.execute()
        for key in PG_ICP_KEYS.values():
            val = self._icp().get_param(key)
            self.assertIn(val, (True, 'True', 'true', '1'),
                          f"ICP key {key} did not round-trip to truthy; got {val!r}")

    def test_settings_form_reads_back_current_icp(self):
        """If the ICP is set externally, opening the settings form must show
        the same value via the default_get() path."""
        self._icp().set_param('productgraph.eco_sla_enabled', 'False')
        # default_get reads the ICP for config_parameter-backed fields.
        defaults = self.env['res.config.settings'].default_get(
            ['pg_eco_sla_enabled'],
        )
        self.assertEqual(defaults.get('pg_eco_sla_enabled'), False)

        self._icp().set_param('productgraph.eco_sla_enabled', 'True')
        defaults = self.env['res.config.settings'].default_get(
            ['pg_eco_sla_enabled'],
        )
        self.assertEqual(defaults.get('pg_eco_sla_enabled'), True)
