# Third-party notices

AI-DLC Studio is licensed under the Apache License 2.0 (see `LICENSE`).

## Bundled AI-DLC Workflows distribution

`payload/aidlc-kiro/` contains a verbatim copy of the Kiro CLI distribution (`dist/kiro`) of
**AI-DLC Workflows 2.7.1** by Amazon.com, Inc. or its affiliates, licensed under the
**MIT No Attribution (MIT-0)** license. The full license text is `payload/AIDLC-LICENSE`.

- Upstream project: https://github.com/awslabs/aidlc-workflows
- Vendored version: `2.7.1` (`AIDLC_VERSION` in `.kiro/tools/aidlc-version.ts`)
- Source commit: `a277af218f0df7f325d3b8be7b6d90fce2c5bd40`
- File inventory and SHA-256 digests: `payload/manifest.json`
- Nothing in the payload is modified. `scripts/build_payload_manifest.py` regenerates the digest inventory,
  and a test fails if a payload file's digest no longer matches the manifest.

AI-DLC Studio installs this payload into a repository only when the user explicitly asks for it, and records
every file it wrote in an install receipt so it can verify, retire, or roll back exactly what it owns.

## Host-provided runtime modules (not redistributed)

The dashboard UI resolves `react`, `react-dom`, `react/jsx-runtime`, `lucide-react`, `@kirocrew/app-sdk` and
`@kirocrew/app-sdk/ui` from the KiroCrew dashboard's import map at runtime. They are marked external in the
build and no copy of them ships in this repository.
