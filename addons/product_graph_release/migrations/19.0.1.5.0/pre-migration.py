# -*- coding: utf-8 -*-
"""Pre-flight for PG A6.1 — guards the new partial UNIQUE index.

19.0.1.5.0 adds ``pg_release_completed_ebom_rev_uniq_idx``, a PARTIAL UNIQUE
index on ``pg_release (ebom_id, revision_id) WHERE state = 'completed'``
(see ``PgRelease.init()``). ``CREATE UNIQUE INDEX`` fails outright if any
duplicate (ebom_id, revision_id) pair already has 2+ 'completed' rows.

This script runs BEFORE the ORM loads and repeats the same duplicate scan
defensively at deploy time (the operator should also have run it manually
pre-deploy per the rollout runbook). If duplicates are found, it aborts the
upgrade with a clear reconcile instruction rather than letting init() fail
with an opaque IntegrityError deep in module loading.

Idempotent — safe to re-run. Emits an INFO log when the scan is clean.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT ebom_id, revision_id, array_agg(id ORDER BY id) AS dup_ids
        FROM pg_release
        WHERE state = 'completed'
        GROUP BY ebom_id, revision_id
        HAVING count(*) > 1
    """)
    dupes = cr.fetchall()
    if not dupes:
        _logger.info(
            "product_graph_release 19.0.1.5.0 pre-migration: no duplicate "
            "completed (ebom_id, revision_id) pairs found — safe to create "
            "pg_release_completed_ebom_rev_uniq_idx."
        )
        return

    lines = [
        f"  ebom_id={ebom_id} revision_id={revision_id} pg.release ids={dup_ids}"
        for ebom_id, revision_id, dup_ids in dupes
    ]
    raise Exception(
        "product_graph_release 19.0.1.5.0 pre-migration ABORTED: found "
        f"{len(dupes)} (ebom_id, revision_id) pair(s) with more than one "
        "'completed' pg.release row. The new partial UNIQUE index "
        "(pg_release_completed_ebom_rev_uniq_idx) cannot be created until "
        "these are reconciled manually:\n" + "\n".join(lines) + "\n"
        "For each pair, pick the canonical release to KEEP as 'completed' "
        "and flip the loser(s) to state='failed' with a failure_reason "
        "noting the reconciliation (e.g.:\n"
        "  UPDATE pg_release SET state='failed', "
        "failure_reason='Reconciled duplicate release — see A6.1 migration' "
        "WHERE id = <loser_id>;\n"
        ") then re-run the upgrade."
    )
