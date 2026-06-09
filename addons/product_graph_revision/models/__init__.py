# -*- coding: utf-8 -*-
from . import pg_revision
from . import pg_property_value
from . import pg_document
from . import pg_item        # _inherit to wire revision_ids One2many back
from . import pg_asset       # _inherit to add revision_id (Phase 3 link)
