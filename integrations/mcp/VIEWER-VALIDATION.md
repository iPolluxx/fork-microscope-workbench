# Saved-evidence viewer validation

The graph-led viewer was checked in the actual deployed private ChatGPT pilot on 2026-10-11 using previously recorded public Qwen evidence. The viewer test loaded no model and collected no new samples.

Observed controls: graph and keyboard selection, token-context highlighting, raw/fitted display, strict thresholds, continuation pagination, contrasting text, provenance and fullscreen. [Actual deployed screenshots](../../docs/chatgpt/README.md) show saved evidence; local mockups are not deployment proof.

The supported host file handoff delivered JSON whose original UTF-8 bytes matched the pinned 238,407-byte file and SHA-256. Chrome displayed it inline; native device Save As remains separately unconfirmed. A prepared link or navigation is not proof of device saving.

The resource retains empty connection/resource/frame-domain allowlists. Model-free suites cover ownership, resource metadata, bounded reads, exact-byte checks, malformed/changed data, bridge origin/tool allowlists, missing host APIs, stale updates, timeout and sanitized errors. Actual-host rendering does not certify independent-user isolation or upload retention. Keyboard/DOM checks are not a screen-reader test; desktop tests are not physical-touch acceptance. [Validation scope](../../docs/chatgpt/VALIDATION.md).
