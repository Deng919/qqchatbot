# Secondhand category dashboard implementation plan

> Execute inline with TDD and a fresh final review. The user opted out of repeated approval questions; their examples are not a three-category specification.

**Goal:** Make the secondhand page browsable by the actual goods in the archive, with a category dashboard and product cards.

**Architecture:** Keep Python/Jinja/vanilla JS. Compute conservative categories at read time from each item's title and its own evidence/components. No migration, extraction rerun, remote AI or new dependency. Category facets and transaction lanes are independent filters, both before pagination.

**Design choices:** Use the observed types: food/drinks, electronics, books/stationery, home, fashion/beauty, sport/transport, online resources, tickets/services, and other/mixed. These are implementation choices, not categories dictated by the user. A package remains one item; mixed or uncertain packages stay other/mixed. Digital membership is separate from physical goods and offline services. Classification is heuristic and may remain unknown.

**Constraints:** Preserve seven-day default, source evidence, bundles, repeat folds, block/favorite actions and authenticated raw API compatibility. Do not touch unrelated untracked files. Use independent Chromium, never automate/close Codex's browser. Ship and push only after verification.

## Review focus

- Product names containing ambiguous brand/food words must not become online goods without evidence.
- Mixed packages remain intact; unrelated items in a seller's full advertising message cannot classify a particular item.
- Category/lane counts reflect active search/date/group/status/price/favorite filters and all pages.
- Seller promotions in different categories must remain reachable from each category; individual prices and source IDs survive.
- Small windows, long names and expanded history must not collide with filters/actions; stale category requests cannot repaint the selection.

## Tasks

- [x] 1. Add failing service/API tests for categories, ambiguity, mixed bundles, promotion boundaries and facet pagination. Implement `category='all'`, item `category`, `categories=[{id,label,count}]`, and category-scoped `lane_counts`; verify focused secondhand suite.
- [x] 2. Add failing JS category interaction tests. Build a desktop category rail and two-column product grid, with transaction summary buttons above and responsive category controls. Keep all previous list/detail actions. Verify source app in an independent browser, inspect desktop/mobile screenshots.
- [x] 3. Fresh review, full pytest/CJS, packaged EXE interaction check and package/source comparison; activate through the existing desktop updater, check live page, commit/push current work branch and verify remote SHA.

**Commands:** `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_secondhand*.py -q` (explicit file list in PowerShell); `node tests/secondhand_ui.cjs`; `D:\CodexTools\python\Scripts\python.exe -m pytest -q`; all `tests/*.cjs`; `scripts/build_desktop.py --release-dir D:\Apps\QQDigestDesktop-2026-10-19-SecondhandDashboard-Release`.

**Evidence:** `D:\Cache\QQDigestSecondhandDashboard-20261019`. Use SQLite backup to private QA data, never destructive production writes.

## Execution record

- Observed category/API and JS interaction tests RED before implementation; focused suite now 104 passed, one upstream Starlette/httpx deprecation warning. All 40 CJS scripts pass.
- Fresh review found three P2 category boundaries: explicit electronic/voucher qualifiers, unrelated items in a whole-post evidence quote, and platform brands on physical merchandise. Seven concrete regressions observed RED, then GREEN in the 104-test run. Category evidence now accepts only attached type qualifiers; bare brands remain uncertain. Promotion fixtures explicitly name membership types to test same-category seller folds.
- Real snapshot browser: 103 sale cards, 55 wanted cards and 22 promotion/category cards. The original seller promotions now have separate entries in their relevant categories; original source IDs/prices remain available. Nine sale category counts sum to 103; uncertain/mixed sale cards: 6. These are heuristic browsing categories, not verified extraction labels.
- Chromium verified all nine categories, lane composition, pagination reset, original source context, favorite toggle/restore, block/unblock facet counts, package components, search, expanded filters, and geometry at 320/390/650/768/900/1024/1280/1440 widths. No page script errors or overlapping controls.
- Complete-suite verification, immutable desktop build, isolated EXE validation and production activation are pending their receipts.

- Immutable desktop package matches 118 modules, 55 templates and 2177 manifest entries; copied actual EXE passed the same browser interaction/geometry checks. Only the owned test EXE was stopped. Full project test receipt remains pending.

- Completed with the 2026-10-20 reading correction:1710 passed/2 skipped,40 CJS, combined actual EXE/package checks and production browser checks pass. Desktop backend `2026-10-20-reading-dashboard`; configuration unchanged, no archive rebuild. Final evidence is in `D:\Cache\QQDigestReadingLayout-20261020`; release is `D:\Apps\QQDigestDesktop-2026-10-20-ReadingDashboard-Release`.
