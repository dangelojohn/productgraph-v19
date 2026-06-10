"""CAD connector drivers — each driver inherits BaseCadConnector and
registers itself in CONNECTOR_REGISTRY."""
from .base import BaseCadConnector, CadDocument, CadBomLine, CONNECTOR_REGISTRY
from . import json_file  # registers JsonFileConnector
