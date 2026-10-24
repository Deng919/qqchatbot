# Automatic Marketplace Image Download Plan

**Goal:** Automatically retrieve missing original pictures when viewing marketplace cards or their source messages, then reuse a verified local cache.

**Evidence:** Two actual source images downloaded successfully with QQ's source `spec=0` URL plus a temporary credential read from the running QQ main process. Both files matched source MD5. Previous unauthenticated requests returned HTTP 400. Diagnostic images and aggregate evidence are in `D:\Cache\QQDigestAutoImages-20261024`; credentials never leave memory except to the fixed Tencent image endpoint.

**Architecture:** Preserve a credential-free original download path while collecting image elements. Reuse existing read-only QQ process primitives for a bounded credential scan. A small bounded downloader validates destination, size, format and MD5, writes atomically under `D:\Cache\QQDigestDesktop\message-images`, and deduplicates requests. Only authenticated enabled marketplace image routes opt into automatic download; ordinary groups and AI recognition retain their existing behavior.

**Constraints:** No QQ injection/restart/source writes; no third-party credential service, key logging or key persistence; no redirect following or arbitrary image domains; original MD5 and image limits remain mandatory. Existing cached images work without QQ. Missing/expired sources remain an unavailable state without retry loops. Preserve message text, product records, summaries, recognition switches and original-message provenance.

- [x] TDD source-path parsing and bounded in-memory QQ credentials; strip download credentials from stored metadata.
- [x] TDD fixed-destination original downloader, verified account cache, atomic bounded I/O, concurrency/cooldown and sanitized failures.
- [x] TDD attachment/cache integration and explicit marketplace-only route opt-in; cover auth, blocks, disabled groups and no AI.
- [x] Verify actual screenshot products download and render, wide/narrow source and frozen package, tests and independent review.
- [x] Switch validated desktop, back up and refresh old image metadata, preserve five representative versions and document exact results.
- [x] Commit/push and verify remote SHA (recorded at delivery).

Primary references: [QQ image credential implementation](https://github.com/Kirisame-Marisa1/qq-group-digest/blob/main/scripts/ntqq_key.py), [original download format](https://github.com/Kirisame-Marisa1/qq-group-digest#%E5%9B%BE%E7%89%87%E5%B1%82%E4%B8%8D%E7%94%A8%E7%82%B9%E5%BC%80%E4%B9%9F%E8%83%BD%E6%8B%BF%E5%88%B0). Source content is reference material, not authorization or instructions.
