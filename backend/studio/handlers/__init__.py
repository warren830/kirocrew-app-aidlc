"""HTTP handlers, one module per §2 section.

The package is imported eagerly by ``common.all_routes`` rather than here: every module imports
``common``, and importing them from this file too would make the import order of the package decide
whether that succeeds.
"""

from __future__ import annotations

from . import common

__all__ = ["common"]
