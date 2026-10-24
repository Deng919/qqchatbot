# Marketplace Source Photos Implementation Plan

**Goal:** Show authenticated source-message pictures in marketplace cards/detail and recover images omitted by the searchable-text collector.

**Evidence:** Marketplace cards have no image rendering; legacy marketplace GET image endpoint incorrectly depends on the AI recognition toggle. Real NTQQ text and image-message snapshots have different coverage. Verified file catalog maps exact group/message IDs, times and image MD5; many current posts have no same-ID image reference.

**Constraints:** Keep display separate from AI. Preserve authentication, enabled-group and blocked-source checks, source-path separation for forwarded children, MD5/format/size verification, and original message text. Do not assign unrelated pictures to product posts. Never write QQ source databases. Use existing archive ingest/dedup for supported references; verify copied data before any production repair.

- [x] Add source-key/path scoped, bounded image metadata to marketplace list/detail; original GET works without enabling recognition. Regression tests cover no AI, auth, blocks, forwarded siblings, integrity and limits.
- [x] Add compact lazy source-photo strips to cards/detail using existing dense layout, safe local URLs and graceful missing-image handling. Verify real browser desktop/mobile.
- [x] Read the exact group image catalog in the collector, preserve richer source metadata, add omitted standalone image messages and mark incomplete sources faithfully. Validate actual source coverage without exposing message bodies or credentials.
- [x] Keep strict original URLs and offer an explicitly labeled local cache preview. Restrict preview lookup to this account's digest-named cache, validate path/size/format/pixels, strip metadata, encode bounded JPEG off the event loop, and never send previews to AI automatically.
- [x] Review correctness and run related Python/frontend suites; validate source and packaged native image browsing.
- [x] Build and switch the verified desktop release, preserve five representative versions, document actual data recovery and limits.
- [x] Commit/push and verify remote SHA (delivery records the verified commit).
