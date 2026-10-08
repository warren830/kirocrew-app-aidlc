"""HTTP handlers, one module per §2 section.

``common.all_routes`` imports the handler modules named in ``common.ROUTES``; importing them here too would
let the package's import order decide whether that succeeds, since every one of them imports ``common``.
"""

from __future__ import annotations

from . import common

__all__ = ["common"]
