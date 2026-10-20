# Reading empty-state layout correction

**Request:** Fix the supplied reading-page layout without changing report generation or stored content. Keep the pending secondhand dashboard work and deliver both.

**Observed causes:** At 2048px viewport, main region is1838px but content is1360px because inherited `width:min(...)` survives `max-width:none`. Empty list remains319px while buttons produce353px scroll width; adjacent reading pane has nothing to show.

**Minimal design:** Reading content fills its main region. No selected point / group previews use one list region, while a selected point or full report retains the reading pane. Empty-state actions wrap within a centered card. Preserve existing navigation, management, source panels, responsive back button and stored filters.

- [x] Observe JS state regression and independent Chromium geometry failure before edits.
- [x] Add one derived layout class; fix reading width and empty action wrapping; verify empty, search-empty, populated, group, full-report and management flows across viewport sizes.
- [x] Fresh review, related tests and full suite, combined immutable desktop release, isolated EXE, production page and GitHub push.

Evidence: `D:\Cache\QQDigestReadingLayout-20261020`. Combined build: `D:\Apps\QQDigestDesktop-2026-10-20-ReadingDashboard-Release`. Previous 2026-10-19 dashboard build remains unactivated; its last interrupted full-suite run has no success receipt and is not treated as a passing run.

## Verification so far

- JavaScript regression observed RED for the missing empty-list class. Independent Chromium observed RED at2048px: main1838/content1360, list319/scroll353. After the fix, content1838/list1838/scroll1838; all empty actions stay within the card.
- An added populated-state assertion initially retained the preceding unmatched search in its fixture. Corrected the fixture to clear the search before asserting restored selection; no production behavior changed for that fixture issue.
- Reader-related tests:30 passed, one upstream Starlette/httpx deprecation warning. All40 CJS pass.
- Independent Chromium: eight width/height combinations, including2048x1258,901x560 and320x700. Empty, expanded filters, real points, no search matches, full reports from points/groups, management, generation panel and mobile back pass, with zero script errors.
- Fresh read-only review: no confirmed issues. Preserved the pre-existing full-report split view as scoped; release and deployment checks remain owned by this execution.
- Test tooling now uses the actual default seven-day window, rather than yesterday's fixed dates, for dashboard facet comparisons. Crossing midnight changes the default snapshot counts to78 sale/47 wanted/19 promotion cards; no data was removed.

## Final delivery

- Full suite:1710 passed,2 skipped,1 upstream deprecation warning; exit0,677.51s. All40 CJS pass.
- Combined immutable release:118 runtime modules/55 templates/2177 manifest files match source. Actual isolated EXE passed both reading and marketplace flows.
- Activated `2026-10-20-reading-dashboard` at `D:\Apps\QQDigestDesktop-2026-10-20-ReadingDashboard-Release`. Prior desktop was not running; activation backed up its registry/shortcut, switched the registered entry and launched only the verified new executable. Config unchanged; archive not rebuilt.
- Production read-only browser checks passed for both areas, including eight reader viewport combinations and exact original marketplace messages. Reader visit tracking was intercepted in test contexts to avoid changing the user's last-opened state. Only owned preview/test processes were stopped.
- Deployment helper corrected its UTF-8 JSON reading on this Windows locale; no application source changed after the successful full suite/build.
