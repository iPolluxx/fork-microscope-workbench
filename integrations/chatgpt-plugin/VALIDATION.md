# Plugin packaging verification

Tests cover deterministic allowlisted archives, per-connection mapping, private permissions, symlink/malformed/tampered imports, duplicate imports and a credential-free child environment. Archives exclude evidence, profiles, tokens, logs, caches and absolute host paths.

Real stdio tests launch the generated private launcher, discover tools, report the deliberately public saved Qwen example and reconstruct/export its unchanged bytes. No worker or provider is started. FastMCP tests must run without skips before claiming transport acceptance.

A manifest or generated ZIP is not proof of fresh-account installation or public-directory acceptance. Operators must build their own registered app mapping and check a fresh chat. [Setup](README.md), [protocol verification](../mcp/PROTOCOL-VALIDATION.md), [viewer acceptance](../mcp/VIEWER-VALIDATION.md), [branch checks](../../docs/chatgpt/VALIDATION.md).
