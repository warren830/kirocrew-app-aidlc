"""AI-DLC Studio backend package.

Imported through the private namespace ``_aidlc_studio_backend.studio`` that ``backend/routes.py``
registers, so the modules below are reachable as attributes (``studio.services``,
``studio.handlers.common``) from the two thin entry points the host loads.

The imports are eager on purpose: a syntax or wiring error should surface while the gateway is
registering the app — where it is logged and reported as degraded hook health — rather than on the
first request a user makes. They are ordered bottom-up (foundation, store, readers, model, lanes,
container, HTTP) so a cycle shows up here as an ImportError naming both ends rather than as a
half-initialised module later.
"""

from __future__ import annotations

from . import constants, errors, security          # foundation: no intra-package dependencies
from . import storage                              # durable store (CAS, leases, events, activity)
from . import aidlc_reader, engine, git_observer    # readers over someone else's files and tools
from . import consistency, repo_registry            # judgements over what the readers found
from . import activity, events, leases, projection, settings  # the read model and Studio's own records
from . import sessions                                          # the host bridge (read-mostly)
from . import actions, notifications, reconciler                 # the action lanes and their delivery
from . import advisor, estimates, installer, migration, plan      # the operations built on top
from . import services                                            # the object graph and its lifecycle
from . import handlers                                            # the HTTP surface (all_routes)

__all__ = [
    "actions",
    "activity",
    "advisor",
    "aidlc_reader",
    "consistency",
    "constants",
    "engine",
    "errors",
    "estimates",
    "events",
    "git_observer",
    "handlers",
    "installer",
    "leases",
    "migration",
    "notifications",
    "plan",
    "projection",
    "reconciler",
    "repo_registry",
    "security",
    "services",
    "sessions",
    "settings",
    "storage",
]
