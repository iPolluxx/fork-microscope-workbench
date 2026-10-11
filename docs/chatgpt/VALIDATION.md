# ChatGPT app validation scope

Validation distinguishes code/protocol checks, the actual deployed saved-evidence viewer, and fresh model execution. A generated mockup, synthetic fixture or old completed job cannot substitute for the latter.

## Isolated source checks — 2026-10-11

- **526 Python checks passed**, with two optional private-fixture tests skipped. This covers private adapter/package tests and the hosted/pilot account, approval, OAuth, storage, worker, provider, lifecycle, resource and packaging suites. Providers and authentication are injected fakes where appropriate; actual FastMCP stdio/in-process protocol tests ran. The first snapshot pass exposed three missing dependency/helper files; including them made all three targeted checks pass.
- **17 additional outcome-readout checks passed**, covering answer matching and completion/channel handling after lazy model imports.
- **Eight JavaScript suite files passed**: viewer bridge, measurements, viewer UI, compute UI, authenticated client, credentials, import and device delivery. These use saved/synthetic data and fake DOM/host/transport; they are not browser or provider acceptance.
- The deliberately public portfolio bundle passed the native validator; raw counts were independently extracted and match [the case study](ATTENDEE-CASE-STUDY.md). The small first-run configuration passed the coordinator validator.
- The static dashboard built successfully. The lightweight pilot packaging tests verified its source allowlist and content-addressed viewer identity. No image was built or deployed for this publication.

These checks used existing dependencies read-only with Python 3.13.13, FastMCP 4.0.3 and MCP 2.2.0. They do not establish a fresh-machine installation, a full-repository scientific test pass or GitHub CI success. The CI workflow includes the new adapter/package and viewer suites for future runs.

## Actual deployed saved-evidence viewer

The 2026-10-11 private ChatGPT acceptance exercised real graph pointer/keyboard selection, original token context, smoothing, strict thresholds, continuation paging, contrasts, provenance and fullscreen. The [three screenshots](README.md) were captured from the deployed component in Chrome, cropped to remove unrelated chat/account details, and otherwise preserve actual pixels. Their source is the repository's already public saved Qwen example. No new sampling occurred during capture.

The source in this branch builds resource `ui://fork-microscope/saved-evidence-e6f0f7d8e3917624.html`, full HTML SHA-256 `e6f0f7d8e3917624a4a5e67dbdbb8bd5fb76120c1ea573947cb215a32457f50a`, matching the saved-viewer resource accepted in the deployed host. The public example project substitution affects cloud assembly, not viewer bytes.

The supported host file handoff delivered original JSON with the expected 238,407-byte length and SHA-256. Chrome opened it inline; native device Save As was not confirmed. The screenshot of an export control does not itself demonstrate download delivery. Host file retention and independent-account access are not certified by this one-account viewer test. Keyboard/DOM acceptance is not screen-reader or physical-touch acceptance.

## Fresh managed execution remains incomplete

A fresh managed trial reached job completion and produced an artifact, but the private-library save returned `INVALID_ARGUMENT`. This showcase contains no new private run archive and makes no claim of successful fresh execution → durable save → verified cleanup acceptance. Saved-viewer screenshots remain historical evidence. Investigate the save failure and test the complete lifecycle before promoting the managed path to a wider release.

The local source tests model successful saves and failures independently. They cannot prove compatibility of an already deployed, pinned worker image with the deployed save contract. Production compatibility and cleanup must be checked on the exact approved session; never weaken evidence identity checks to make an incompatible artifact pass.

## Publication boundary

Published: the project's own app/plugin source, tests, setup/architecture docs, an existing deliberately public demo, a separate config example and three redacted-by-crop deployed screenshots. Excluded: new private evidence, account/provider identifiers, app/tunnel mappings, conversation URLs/transcripts, credentials, signed capabilities, runtime state and bundled upstream research code/weights. Root MIT and third-party notices remain in place. The one-account cloud factory and its fake tests use a public example project identifier, preserving the fixed-project guard without publishing the private deployment binding.
