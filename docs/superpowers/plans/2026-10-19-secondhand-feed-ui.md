# Secondhand feed UI correction

> Execute inline using executing-plans and TDD. The user has requested the visible UI correction directly and previously opted out of repeated approval questions.

**Goal:** Make the list visibly shorter and actionable instead of repeating items with labels.

**Design:** Use the existing Python/Jinja/vanilla JS stack with no new stored state or database migration. Three list tabs: idle goods (default), wanted, and suspected promotion/services. Collapse related seller/title publications before pagination; collapse promotion listings by seller. These are presentation groups, not assertions that every physical item is identical. Preserve each original item ID, source, price, favorite and block action. Unknown author or original date cannot cause cross-source grouping. Same-post bundles/collections expose component names in inline details; repeated posts expose a publication timeline inline. Keep normal filters, seven-day defaults and authenticated routes.

**Files:** `qq_digest/secondhand.py`, `qq_digest/web/secondhand_routes.py`, secondhand Jinja/JS templates, `tests/test_secondhand_feed.py`, `tests/secondhand_ui.cjs`, verification/roadmap docs.

**Review focus:** grouping before pagination; unknown seller/time isolation; hiding promotion only in normal lanes while counts remain visible; original member IDs/prices kept in publication history; filters applied before counts; source text rendered safely; responsive tabs/actions do not collide.

- [x] Add and observe failing service/API tests for grouped repeat rows, seller promotion folds, wanted lane, before-pagination counts, unknown identity isolation and preservation of individual IDs/prices.
- [x] Implement an optional grouped read view, keeping the ungrouped service/API available for compatibility and evidence inspection. Do not mutate archived data.
- [x] Add UI interaction regressions, then build tabs, compact cards, inline components/history and card-level block actions. Verify in an independent browser against a private snapshot; inspect screenshots.
- [x] Run full pytest and CJS, fresh final review, packaged EXE and actual page verification; switch the desktop through the existing updater, commit and push the current GitHub work branch. Do not stage unrelated untracked files or user data.

**Verification output:** `D:\Cache\QQDigestSecondhandFeed-20261019`. Desktop build target: `D:\Apps\QQDigestDesktop-2026-10-19-SecondhandFeed-Release`.


## Execution record

- Six grouped-service tests observed RED for the missing grouped view, then GREEN; API compatibility/lane validation and CJS inline-disclosure/card-action regressions pass. Related regression suite: 57 passed.
- Same original 256 listings are now 103 sale cards, 55 wanted cards and 11 seller-level promotion cards. No market row was rebuilt, deleted or reidentified.
- Independent Chromium and the actual copied EXE passed all lane, disclosure, source-context, favorite, block/unblock, component, filter and pagination flows at 320/390/768/900/1024/1440 widths. All 40 CJS scripts passed.
- Desktop package matches 117 runtime modules, 55 templates and 2177 manifest files. The maintenance-only archive repair module is excluded as in the previous release.
- Fresh reviewer: no reproducible important findings; additionally verified favorite/price subsets and unknown-original-time isolation.
- QA-only setup: corrected the local test secret length and disabled automatic cleanup on the copied archive to avoid background-cleanup lock contention during interaction tests. Production preferences/configuration were not changed.
- Final full-suite verification and desktop activation remain gated on a zero exit receipt.

- Complete: final suite 1663 passed/2 skipped, source and packaged browser flows green, actual desktop activated and production read-only UI verified at 103/55/11 with zero script errors. No archive rebuild or production configuration changes.
- Existing program was not running; activation backed up and updated only its registered shortcut/version record, then launched the verified new executable. No unrelated processes were closed.
