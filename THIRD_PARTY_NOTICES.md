# Third-party notices

AI-DLC Studio is licensed under the Apache License 2.0 (see `LICENSE`).

## Bundled AI-DLC Workflows distribution

`payload/aidlc-kiro/` is the Kiro CLI tree (`runtime/kiro`) of the manual-copy release asset
`aidlc-copy-runtime-2.10.0.tar.gz` of **AI-DLC Workflows 2.10.0** by Amazon.com, Inc. or its
affiliates, licensed under the **MIT No Attribution (MIT-0)** license. The full license text is
`payload/AIDLC-LICENSE`. From 2.8.0 the upstream repository no longer commits `dist/<harness>/`;
the copy-runtime asset is its Bun-based equivalent. The bundled copy includes one Studio-maintained
local change and is not byte-identical to upstream.

- Upstream project: https://github.com/awslabs/aidlc-workflows
- Vendored version: `2.10.0` (`AIDLC_VERSION` in `.kiro/tools/aidlc-version.ts`)
- Upstream release commit: `2a883858f5483bce3b48f43b8f6d3ca2c042d6ae` (tag `v2.10.0`); the asset's
  published SHA-256 sidecar was verified before extraction
- File inventory and SHA-256 digests: `payload/manifest.json`
- Local compatibility patches are identified in the manifest's `source.ref`; `source.commit` and
  `engineVersion` continue to identify the upstream base. `scripts/build_payload_manifest.py`
  regenerates the digest inventory, which the installer verifies against the bundled bytes.

The local requirement-traceability patch changes
`payload/aidlc-kiro/.kiro/tools/aidlc-sensor-traceability.ts`. Units-generation maps only IDs found in
its selected upstream source: US IDs from an existing `stories.md`, or FR group/detail IDs from
`requirements.md` when stories are absent. When stories are absent, NFR IDs that `requirements.md`
names may also be joined and covered without being required, as upstream 2.10.0 does. A story-map row
maps only the IDs in the columns its header names as ID columns to the units in its unit columns;
notes, rationale and dependency columns never map anything, and a table without such a header maps
only the IDs in the first cell that names one. Functional-design and, when stories are absent,
code-generation apply an existing requirement map to the current unit; a unit may still list another
unit's requirement as `N/A`. Without a map, or with a map that has no requirement rows, both keep the
existing every-requirement fallback. Existing story maps still resolve to acceptance-criterion IDs.
Missing mappings, wrong units, and IDs outside the selected source remain failures. No US stories
are synthesized. This is a Studio vendored fix layered on upstream 2.10.0's own changes to the same
file (zero-Unit code generation, NFR joins); upstream's `tests/unit/t281-sensor-traceability.test.ts`
passes against the patched file.

The plan-progress and review-appendix patches carried with 2.7.1 were retired: 2.10.0 computes the
Code Generation plan-approval fingerprint over a projection that resets task markers, drops a terminal
`## Review` appendix and normalises editor whitespace, and reviewers no longer append to the plan.
`tests/test_plan_progress_fingerprint.py` now pins that upstream behaviour.

Regression coverage lives in `tests/test_traceability_fallback.py` and runs the bundled sensor with
Bun against temporary fixtures. On a future upstream refresh, reapply this patch or verify that
upstream supplies the same behavior by running those tests before regenerating the manifest.

AI-DLC Studio installs this payload into a repository only when the user explicitly asks for it, and records
every file it wrote in an install receipt so it can verify, retire, or roll back exactly what it owns.

## Host-provided runtime modules (not redistributed)

The dashboard UI resolves `react`, `react-dom`, `react/jsx-runtime`, `lucide-react`, `@kirocrew/app-sdk` and
`@kirocrew/app-sdk/ui` from the KiroCrew dashboard's import map at runtime. They are marked external in the
build and no copy of them ships in this repository.
