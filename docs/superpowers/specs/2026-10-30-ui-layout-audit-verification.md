# UI layout audit

## Reported problem and fixes

- Placed the summary point-range dropdown and page-group search in one row with matching 38px heights. Reused the existing feed-tools container. Date shortcuts, view tabs and the group/date disclosure retain separate, predictable positions; narrow layouts keep the two related fields together.
- Opted the group, batch, collection, search and archive-question filters into the existing floating group selector. Opening a filter no longer stretches its toolbar or pushes adjacent controls down. The summary and reminder pickers remain inline where their scrolling containers require it.
- Applied the selector's flex rows and normal label weight to inline selectors as well. Explicitly reset checkbox and radio sizing so broad form-input rules cannot stretch these choices across the dialog. Names can wrap while group numbers remain separate.
- Removed the overview group-statistics table's inherited minimum width. Its three columns now fit the card; the header-only first-column width preserves the existing stacked mobile cells.
- Completed the existing find-interaction test's DOM stub with a `children` array after the broader regression run exposed the missing property. Production find logic was unchanged.

## Verification

- Targeted pytest: **168 passed**, zero failures, errors or skips. The desktop navigation runner includes nine independent Chromium browser scenarios.
- The new layout regression reproduced the original stacked summary controls before the fix. It now checks matching row/height at 1600, 1280, 1024, 768 and 390px, view switching, document overflow, overview table fit, stable floating-filter toolbar heights, unclipped checkbox hit targets, Escape dismissal and reminder choice sizing.
- Manual browser audit covered summary views, overview, market, collections, knowledge, run records, groups, collection, search, archive questions, reminders, settings sections, setup, login and failure states. Checked light/dark themes and desktop/narrow widths. Dedicated summary screenshots explicitly select the points view to avoid remembered view state.
- Settings/setup/overview audit recorded 54 route/theme/width combinations, with additional setup-step, login and failure checks. Group/search checks recorded 24 combinations after the fixes. The main-page audit recorded 48 combinations. These counts describe sampled states, not exhaustive coverage of every data/action combination.
- Independent native packaged-WebView verification passed: summary geometry, overview table, floating filters and checkbox hit targets, existing navigation arrows and active states, persistent page documents, retained search/reading state, source drilldown, settings query and reduced motion. No page errors were recorded.
- Verified source equivalence for 120 packaged Python modules and 62 templates, plus the release manifest containing 2198 files. Fresh read-only diff review found no material issues.

## Scope and limits

Browser and native tests used synthetic local data. No real AI-provider, collection, backup restoration, import, cleanup or configuration-save actions were performed. Unavailable backup/update states and sampled record details do not prove every production-data state correct.

## Locations

- Release: `D:\Apps\QQDigestDesktop-2026-10-30-UiLayoutAudit-Release`
- Backend: `2026-10-30-ui-layout-audit`
- Screenshots, audit results, test XML and native/package evidence: `D:\Cache\QQDigestUiAudit-20261030`
- Desktop shortcut: `D:\Desktop\QQ Digest.lnk`
- Activation uses the existing native updater, preserves the configuration checksum and retains the previous HistoryArrows release for rollback. Activation evidence is stored in `activation-result.json` under the evidence directory.
