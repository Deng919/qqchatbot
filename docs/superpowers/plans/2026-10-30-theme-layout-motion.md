# Theme and layout motion implementation plan

> Execute inline using executing-plans; use one focused fresh reviewer after implementation.

**Goal:** Apply the approved gentle theme change and continuous card filtering/reordering to the desktop application.
**Architecture:** Keep the current renderer and request guards. Add one explicit keyed layout capture/finish helper using WAAPI translation, small entrances and inert short exits; integrate only summary cards, market, bookmarks and its knowledge view. Theme colors transition for 500ms only during an actual theme change, leaving startup instant and respecting reduced motion.
**Tech Stack:** Jinja templates, CSS color transitions, WAAPI, existing independent Playwright runner.
**Spec:** User approved the two reference effects in chat: gentle theme interpolation, cards moving to fill filtered slots, keep the approved deck spread and no new white borders.

## Constraints and review focus

- Use current feature branch; preserve unrelated files and existing data. User already authorized implementation and release in this workflow.
- Capture displayed positions before canceling an interrupted animation. Latest API response must still win.
- Exiting snapshots must be inert, inaccessible, noninteractive and removed at completion, hide or reduced-motion change.
- No conflicting whole-region fade when card-level layout handles the render.
- Hidden desktop documents and reduced-motion settings settle immediately; first paint does not animate theme.
- Keep deck disclosure and source navigation intact. Do not introduce a generic framework or dependencies.

## Steps

- [x] Add browser regression for actual theme color interpolation, rapid reversal, persisted preference, first-paint/reduced-motion behavior, and card reorder/exit/entrance on the real routes. Observe failures.
- [x] Implement `WorkspaceLayout.capture(host)` / `finish(host, before)` in `layout_motion.html`; explicit keyed callers own mutations. Extend refresh controller with `handled(node)` to suppress redundant parent refresh effects. Add theme color transition lifecycle to `appearance.html`.
- [x] Integrate summary query/reload and market/bookmark/knowledge renderers with stable keys. Preserve old market list until response. Test summary expanded filtering, rapid requests and no-match restoration.
- [x] Run existing targeted reading/navigation/appearance regressions, desktop/mobile screenshots and fresh review. Repair concrete failures.
- [x] Build separate `D:\Apps\QQDigestDesktop-2026-10-30-ThemeLayout-Release`; verify package and native WebView2, activate through the updater preserving rollback/configuration.

Delivery: commit/push the verified changes and check the remote branch SHA.

## Ledger

- Ruling: the approved scope and user request to act supply execution authorization; proceed inline without another plan approval prompt.
- Ruling: stay in the current authorized feature checkout, used by the existing preview and release tooling; unrelated untracked files remain excluded.
- Ruling: retain existing renderers and use stable feed/bookmark/knowledge/group IDs; avoid a new virtual DOM or store.
- Evidence/artifact directory: `D:\Cache\QQDigestThemeLayout-20261030`.

- Scope update: user approved the circular meter for summary generation. Add `progress_meter.html` with accessible native progress plus a compact SVG ring; `summaryAnimateMeter(prefix,data)` uses actual completed/total counts, synchronized 450ms interpolation, reversible updates and hidden/reduced settling. Preserve percentage on failure, show 0% for an empty scope, and exclude animated digits from parent refresh observation.
- Repair: action icon decoration changes button dimensions after render; expose the existing decorator's refresh function and finish it before measuring layout. Stable content fingerprints distinguish data changes from decoration.
- Repair: skip nested filter capture during dashboard rebuild; a short session monitor removes fixed exits when the host becomes hidden/detached. Added regressions reproducing both reviewer findings.
- Repair: deck measurement captures displayed card positions then cancels generic layout movement before starting deck motion, avoiding compounded translations. Entrance uses opacity/translation without scaling card geometry.
- Repair: only an operation that actually takes over deck movement cancels the generic session; late inactive-group responses leave the current filter movement running. The regression failed before the repair and now passes in Chromium and packaged WebView2.
- Verification: 159 targeted tests passed with no failures/errors/skips; native reading/navigation, deck, 17 refresh paths, theme/layout and circular progress checks passed. Package verification: 120 modules, 62 templates, no mismatches. Fresh reviewer reports no remaining material findings.
- Activation: successful, configuration checksum unchanged, desktop shortcut updated, rollback points to RefreshMotion-Release. Evidence is in the matching verification document and the artifact directory.
