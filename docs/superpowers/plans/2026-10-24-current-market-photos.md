# Current Marketplace Photos Repair Plan

**Goal:** Make pictures from current marketplace messages available in the actual product list and source context, with verified provenance and honest missing-file states.

**Evidence:** The current encrypted message snapshot decrypts, but global integrity fails on historical records. Refresh retains the August verified output. Exact current message IDs and recent per-group sequence ranges are readable in the diagnostic copy. The previous image catalog repair did not reach most current images. The blanket listing uses text-only source; a same-seller image was sent eleven seconds earlier in the same group.

**Architecture:** Preserve the existing verified message output and its integrity gate. Extract readable current media into a separate clean, explicitly incomplete supplemental snapshot during refresh and merge only exact group/message/timestamp identities in collection. Do not introduce general SQLite repair or invent missing images. The user approved grouping consecutive same-seller photos into cards with their original-message source labels; source contexts remain factual.

**Constraints:** QQ source remains read-only; no AI calls for photo viewing; authenticated/enabled/blocked/forward-source protections and bounded local previews remain. Preserve message text, product records, reports and OCR settings. Use synthetic TDD, then copied real data and the exact screenshot cases. QA outputs stay in `D:\Cache\QQDigestMarketImages-20261024`.

- [x] Add regression and implement transactional, bounded, clean current media supplement in refresh, preserving the verified main database on integrity failure.
- [x] Consume media supplement with exact identity checks, preserve original text and source sequence metadata through archive deduplication.
- [x] Audit the three screenshot cards; implement authorized display association with source labels and ambiguity guards; retain meaningful unavailable-image state.
- [x] Run related Python/frontend suites and actual screenshot-case browser checks, review changes, build and verify package.
- [x] Back up before production repair, switch tested desktop version, preserve five representative releases and record actual results.
- [x] Commit/push and verify remote SHA (recorded at delivery).
