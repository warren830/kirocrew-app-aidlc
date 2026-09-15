# Third-party notices

AI-DLC Studio is licensed under the Apache License 2.0 (see `LICENSE`).

## Bundled AI-DLC Workflows distribution

`payload/aidlc-kiro/` is based on the Kiro CLI distribution (`dist/kiro`) of
**AI-DLC Workflows 2.7.1** by Amazon.com, Inc. or its affiliates, licensed under the
**MIT No Attribution (MIT-0)** license. The full license text is `payload/AIDLC-LICENSE`.
The bundled copy includes Studio-maintained local changes and is not byte-identical to upstream.

- Upstream project: https://github.com/awslabs/aidlc-workflows
- Vendored version: `2.7.1` (`AIDLC_VERSION` in `.kiro/tools/aidlc-version.ts`)
- Upstream base commit: `a277af218f0df7f325d3b8be7b6d90fce2c5bd40`
- File inventory and SHA-256 digests: `payload/manifest.json`
- Local compatibility patches are identified in the manifest's `source.ref`; `source.commit` and
  `engineVersion` continue to identify the upstream base. `scripts/build_payload_manifest.py`
  regenerates the digest inventory, which the installer verifies against the bundled bytes.

The local requirement-traceability patch changes
`payload/aidlc-kiro/.kiro/tools/aidlc-sensor-traceability.ts`. Units-generation maps only IDs found in
its selected upstream source: US IDs from an existing `stories.md`, or FR group/detail IDs from
`requirements.md` when stories are absent. Functional-design applies an existing requirement map
to the current unit before checking FR-to-business-rule coverage; without a map it preserves the
existing requirements fallback. Existing story maps still resolve to acceptance-criterion IDs.
Missing mappings, wrong units, and IDs outside the selected source remain failures. No US stories
are synthesized. This is a Studio vendored fix, not an upstream change, and it is additional to
the previously recorded plan-progress and review-appendix compatibility patches.

Regression coverage lives in `tests/test_traceability_fallback.py` and runs the bundled sensor with
Bun against temporary fixtures. On a future upstream refresh, reapply this patch or verify that
upstream supplies the same behavior by running those tests before regenerating the manifest.

AI-DLC Studio installs this payload into a repository only when the user explicitly asks for it, and records
every file it wrote in an install receipt so it can verify, retire, or roll back exactly what it owns.

## Host-provided runtime modules (not redistributed)

The dashboard UI resolves `react`, `react-dom`, `react/jsx-runtime`, `lucide-react`, `@kirocrew/app-sdk` and
`@kirocrew/app-sdk/ui` from the KiroCrew dashboard's import map at runtime. They are marked external in the
build and no copy of them ships in this repository.
