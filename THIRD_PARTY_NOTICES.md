# Third-party notices

AI-DLC Studio is licensed under the Apache License 2.0 (see `LICENSE`).

## Bundled AI-DLC Workflows distribution

`payload/aidlc-kiro/` is the Kiro CLI tree (`runtime/kiro`) of the manual-copy release asset
`aidlc-copy-runtime-2.11.0.tar.gz` of **AI-DLC Workflows 2.11.0** by Amazon.com, Inc. or its
affiliates, licensed under the **MIT No Attribution (MIT-0)** license. The full license text is
`payload/AIDLC-LICENSE`. From 2.8.0 the upstream repository no longer commits `dist/<harness>/`;
the copy-runtime asset is its Bun-based equivalent. The bundled copy includes one Studio-maintained
local change and is not byte-identical to upstream.

- Upstream project: https://github.com/awslabs/aidlc-workflows
- Vendored version: `2.11.0` (`AIDLC_VERSION` in `.kiro/tools/aidlc-version.ts`)
- Upstream base: branch `release/2.11` at `6a378b53c0a4fe0641ed7d8de8dfff94264d5b6a`, the commit tagged
  `v2.11.0`; the asset's published SHA-256 sidecar was verified before extraction, and the extracted
  `runtime/kiro` differs from the bundle only in the patched file named below
- File inventory and SHA-256 digests: `payload/manifest.json`
- Local compatibility patches are identified in the manifest's `source.ref`; `source.commit` and
  `engineVersion` continue to identify the upstream base. `scripts/build_payload_manifest.py`
  regenerates the digest inventory, which the installer verifies against the bundled bytes.

The local requirement-traceability patch changes
`payload/aidlc-kiro/.kiro/tools/aidlc-sensor-traceability.ts`. Units-generation maps only IDs found in
its selected upstream source: US IDs from an existing `stories.md`, or FR group/detail IDs from
`requirements.md` when stories are absent. When stories are absent, NFR IDs that `requirements.md`
names may also be joined and covered without being required, as upstream does since 2.10.0. A story-map row
maps only the IDs in the columns its header names as ID columns to the units in its unit columns;
notes, rationale and dependency columns never map anything, and a table without such a header maps
only the IDs in the first cell that names one. Functional-design and, when stories are absent,
code-generation apply an existing requirement map to the current unit; a unit may still list another
unit's requirement as `N/A`. Without a map, or with a map that has no requirement rows, both keep the
existing every-requirement fallback. Existing story maps still resolve to acceptance-criterion IDs.
Missing mappings, wrong units, and IDs outside the selected source remain failures. No US stories
are synthesized. This is a Studio vendored fix re-applied on top of upstream 2.11.0's own version of the
same file (zero-Unit code generation, NFR joins); upstream's `tests/unit/t281-sensor-traceability.test.ts`
at `6a378b53` passes against the patched file (27 tests).

The KiroCrew-reply patches carried with 2.10.0 were retired. The adapter patch
(`.kiro/hooks/aidlc-kiro-adapter.ts`) handed the core human-turn hook only the text after the last
`[CURRENT USER REQUEST -- respond to this]` header of a KiroCrew dashboard frame; 2.11.0 reads the
person's turn after that header itself, for every host frame (`hostEnvelopeTurnText`), and keeps their
exact words. The testing-posture patch (`.kiro/tools/aidlc-testing-posture.ts`) took
`Request Changes: <feedback>` as the Request Changes choice; 2.11.0 records a choice only for an exact
pick of an offered option and leaves any other reply, a change request with its feedback included, to
the conversation's agent, which asks the plan-approval question again through the engine.
`tests/test_payload_human_reply.py` now pins that upstream behaviour by running the bundled hooks with
Bun against a temporary install.

2.11.0's copy runtime ships no `AGENTS.md`, `.gitignore`, `aidlc/active-space` or team memory files,
and its `.kiro/settings/cli.json` carries only `chat.defaultAgent`. AI-DLC adds its own marked part
of `AGENTS.md` and `.gitignore` (`<!-- BEGIN AI-DLC:agents -->`, `# BEGIN AI-DLC:gitignore`, from
`.kiro/tools/data/root-blocks/`) at the next session start, so a fresh Studio install writes neither
file. An upgrade from a 2.10 install takes out Studio's own `AGENTS.md` block and the `.gitignore`
lines Studio added, exactly as uninstall would and only while they are unmodified; an edited or
unprovable part stays on disk, is reported in the upgrade preview, and is no longer Studio's. The
2.10 per-model default Studio wrote to `cli.json` (`chat.modelDefaults` → `claude-opus-4.8`) is inert
under 2.11 and stays where it is, unowned, through the upgrade and a later uninstall.

The plan-progress and review-appendix patches carried with 2.7.1 were retired: 2.10.0 computes the
Code Generation plan-approval fingerprint over a projection that resets task markers, drops a terminal
`## Review` appendix and normalises editor whitespace, and reviewers no longer append to the plan.
`tests/test_plan_progress_fingerprint.py` now pins that upstream behaviour.

Requirement-traceability coverage lives in `tests/test_traceability_fallback.py` and runs the bundled
sensor with Bun against temporary fixtures. On a future upstream refresh, reapply the patch or verify
that upstream supplies the same behavior by running those tests before regenerating the manifest.

AI-DLC Studio installs this payload into a repository only when the user explicitly asks for it, and records
every file it wrote in an install receipt so it can verify, retire, or roll back exactly what it owns.

## Host-provided runtime modules (not redistributed)

The dashboard UI resolves `react`, `react-dom`, `react/jsx-runtime`, `lucide-react`, `@kirocrew/app-sdk` and
`@kirocrew/app-sdk/ui` from the KiroCrew dashboard's import map at runtime. They are marked external in the
build and no copy of them ships in this repository.
