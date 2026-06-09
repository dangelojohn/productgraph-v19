# southbrook_plm_productgraph — Bridge Addon Spec

> **This is a spec.** The actual addon is deployed to the QNAP southbrook
> stack as `southbrook_plm_productgraph` (note the flipped naming — sorts
> alongside other `southbrook_*` addons in the addons dir) and tracked
> in the `qnap/southbrook` Forgejo repo, NOT in the public
> `product_graph_v19` repo. Source of truth:
>
> - Disk: `/share/CACHEDEV3_DATA/Container/southbrook/addons/southbrook_plm_productgraph/`
> - Git: `https://southbrookcabinetry.local:9444/git/qnap/southbrook` →
>   `addons/southbrook_plm_productgraph/` (from commit `8ee36fe` onward).
>
> This file documents the interface contract — what the bridge IS and
> ISN'T allowed to do — so any future re-implementation stays compliant
> with Decision D1.

## Purpose (Decision D1)

Glue `southbrook_plm` ECO closure to `pg.release` execution. Strictly **one-way**:

- `southbrook_plm.eco` CALLS into `pg.release.action_execute_release`.
- `product_graph_*` NEVER imports anything from `southbrook_plm`.

This keeps the public ProductGraph addons publishable as a generic OpenBOM-mirror PLM, while letting Southbrook drive releases from their existing ECO workflow.

## Direction of coupling

```
  southbrook_plm.eco       (Southbrook private — cabinet ECO/cut-spec authority)
        │
        │   on close →
        ▼
  pg.release.action_execute_release   (generic ProductGraph LGPL)
        │
        ▼
  mrp.bom + mrp.bom.line               (Odoo core)
```

The bridge MAY:
- Add fields to `southbrook_plm.eco` like `pg_ebom_id` and `pg_release_id`.
- Override `southbrook_plm.eco.action_close` (or whichever transition method closes the ECO) to also fire `pg.release.action_execute_release`.
- Add a smart button on the ECO form linking to the resulting `pg.release`.
- Add demo data / sample wiring.

The bridge MUST NOT:
- Modify any `pg.*` model definitions.
- Add fields to `pg.item`, `pg.revision`, `pg.ebom`, or `pg.release` for Southbrook-specific use. (If you need that, do it on a new `southbrook.*` model that references the pg.* record.)
- Override `pg.release.action_execute_release` — it's the keystone method; the bridge calls it, never replaces it.

## Manifest shape

```python
{
    'name': 'ProductGraph ↔ Southbrook PLM Bridge',
    'version': '19.0.1.0.0',
    'depends': [
        'product_graph_release',   # the generic release flow
        'southbrook_plm',          # Southbrook's ECO authority
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/southbrook_plm_eco_inherit_views.xml',
    ],
    'license': 'Proprietary',      # Southbrook-private
    'installable': True,
    'application': False,
}
```

## Models added

```python
class SouthbrookPlmEco(models.Model):
    _inherit = 'southbrook_plm.eco'

    pg_ebom_id = fields.Many2one(
        'pg.ebom',
        string='Target EBOM',
        domain="[('state','=','released')]",
        help='If set, ECO closure will trigger a pg.release on this EBOM.',
    )
    pg_release_id = fields.Many2one(
        'pg.release',
        string='Triggered Release',
        readonly=True,
        copy=False,
    )
    pg_release_reason = fields.Text(
        string='Release Reason (auto-derived)',
        compute='_compute_pg_release_reason',
        store=False,
        help='Derived from ECO title / description. Editable before close.',
    )
    pg_auto_release = fields.Boolean(
        string='Auto-release on ECO close',
        default=True,
    )
```

## Method to override

```python
def action_close(self):  # the Southbrook ECO closure method
    res = super().action_close()
    for eco in self:
        if eco.pg_auto_release and eco.pg_ebom_id and not eco.pg_release_id:
            release = self.env['pg.release'].create({
                'ebom_id': eco.pg_ebom_id.id,
                'release_reason': eco.pg_release_reason or eco.name,
            })
            release.action_execute_release()
            eco.write({'pg_release_id': release.id})
            eco.message_post(body=_(
                "ProductGraph release <b>%s</b> executed on ECO closure.",
                release.name,
            ))
    return res
```

## Open questions for the user

The bridge skeleton uses guessed names. Confirm:

1. **Exact `_inherit` model name.** Is the Southbrook ECO model `southbrook_plm.eco`, `southbrook.plm.eco`, or something else?
2. **Exact closure method.** Is it `action_close`, `action_implement`, `action_release`, `action_done`?
3. **Field semantics on the ECO.** Does the Southbrook ECO already have a link to a `pg.item`, a `pg.revision`, or only its own cut-spec records?
4. **Trigger granularity.** Per-ECO release, or batch (one release per affected pg.ebom)?

The skeleton makes safe guesses (1 = `southbrook_plm.eco`, 2 = `action_close`). Adjust before installing.

## Testing the bridge

1. Install `product_graph_*` first; verify the generic stack works end-to-end on the QNAP southbrook instance.
2. Install `southbrook_plm` next (already deployed per session memory).
3. Install `product_graph_southbrook` last.
4. Create a Southbrook ECO, link it to a `pg.ebom`, run the ECO through to close, verify a `pg.release` was created and executed.

## Where to put it

```
~/southbrook-v19cr/
└── product_graph_southbrook/
    ├── __manifest__.py
    ├── __init__.py
    ├── models/
    │   ├── __init__.py
    │   └── southbrook_plm_eco.py
    └── views/
        └── southbrook_plm_eco_inherit_views.xml
```

Deploy via the same `deploy-southbrook-plm.sh` recipe used for the existing `southbrook_plm` addon.
