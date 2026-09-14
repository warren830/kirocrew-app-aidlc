# `aidlc-console` prototype archive

The read-only prototype AI-DLC Studio replaces. Kept here because this directory held the only copy of
its source (the app was installed from it, so `~/.kiro/crew/apps/aidlc-console/` is the other copy) and
because the migration path (PRD FR-MIG-001…005) has to read its storage schema.

| File | What it is |
|---|---|
| `aidlc-console-app.json` | the prototype manifest: slug `aidlc-console`, route `/aidlc-console`, hand-written `ui/index.mjs` |
| `aidlc-console-routes.py` | the single-file backend: state-file parser, seven consistency findings, repo registry |
| `aidlc-console-ui.index.mjs` | the no-build ESM dashboard page |
| `aidlc-console-repos.json` | a real `data/kv/repos.json`: the exact shape the migration reads (`{id, path, label, added_at}`, `id = sha256(realpath)[:12]`) |

What carries forward is documented in `docs/research/07-prototype-and-migration.md`. Nothing here is
imported by Studio; the migration reads the *installed* app's storage, never this copy.
