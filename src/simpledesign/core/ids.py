"""Stable internal IDs. Names are only for display; formulas store IDs."""

from __future__ import annotations

import re
import uuid

ID_RE = re.compile(r"^[0-9a-f]{32}$")


def new_id() -> str:
    return uuid.uuid4().hex
