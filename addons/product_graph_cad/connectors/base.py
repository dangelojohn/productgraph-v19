# -*- coding: utf-8 -*-
"""CAD connector base class + payload shapes.

Every CAD / PDM driver inherits :class:`BaseCadConnector` and registers
itself by appending ``(code, class)`` to :data:`CONNECTOR_REGISTRY`. The
:class:`CadDocument` / :class:`CadBomLine` dataclasses define the wire
shape — a real SOLIDWORKS PDM driver and the JSON-file reference driver
return the same payload, which is what makes the contract real.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CadBomLine:
    """One row of a parent's BOM. ``ref_designator`` is optional but
    populated when the CAD source carries it (e.g. R12 / C4 on a PCB)."""
    child_part_number: str
    qty: float = 1.0
    ref_designator: str = ''


@dataclass
class CadDocument:
    """One document worth of engineering data — the unit a driver returns
    per ``pull_document()`` call.

    The ``ebom`` list is one level. Recursion across sub-assemblies is the
    driver's responsibility; the importer treats each document as a single
    parent + its direct children.
    """
    part_number: str
    name: str
    description: str = ''
    item_type: str = 'part'  # one of pg.item.item_type Selection values
    properties: dict[str, Any] = field(default_factory=dict)
    ebom: list[CadBomLine] = field(default_factory=list)


class BaseCadConnector:
    """Abstract driver. Subclasses must implement the three methods below.

    The constructor receives the ``pg.cad.connector`` record and the
    Odoo ``env`` so drivers can read their config off the record's
    ``config_json`` field.
    """

    #: Stable string identifier — used in the connector record's ``code``
    #: field and as the registry key. Drivers must override.
    code: str = ''

    #: Human-readable label shown in the UI.
    label: str = ''

    def __init__(self, record):
        self.record = record
        self.env = record.env

    # ──────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────
    def fetch_metadata(self) -> dict[str, Any]:
        """Return a self-description for the action_test_connection
        UI. At minimum: ``{'code': ..., 'reachable': bool, 'detail':
        str}``."""
        raise NotImplementedError

    def list_documents(self) -> list[CadDocument]:
        """Return every document this connector is currently exposing.
        The reference JSON driver returns the full set on each call;
        a SOLIDWORKS driver might filter by checkout state."""
        raise NotImplementedError

    def pull_document(self, ref: str) -> CadDocument | None:
        """Load one specific document by its CAD-side reference. The
        ``ref`` shape is driver-specific (file path, EPDM EFolder id,
        OnShape did/v/wid triple). Return None if not found."""
        raise NotImplementedError


#: Registry of (code, BaseCadConnector subclass). Importing each driver
#: module appends to this list as a side-effect so the framework can find
#: drivers by code at runtime.
CONNECTOR_REGISTRY: list[tuple[str, type[BaseCadConnector]]] = []
