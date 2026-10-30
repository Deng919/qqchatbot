# Desktop navigation trial verification

## Delivered behavior

The Windows launch opens an authenticated `/desktop-workspace` host. Allowlisted application documents remain alive in same-origin frames. Returning to a visited page preserves inputs, scroll, reading detail and dialogs. Existing standalone web navigation remains available.

Only page content moves; each retained document keeps its original sidebar. Interruptible springs preserve position and velocity during rapid switches. The shared sidebar indicator follows the active entry. Settled content removes its transform to preserve fixed overlays. Reduced motion takes effect immediately. No new white outline or page opacity blending was added.

The host coordinates reading and browser history, dirty-form exit protection, successful mutation refreshes, session reauthentication, load failures and retries. A host-instance identifier prevents stale frame IDs in browser history from referring to a different document after reload. Saved bookmarks and knowledge bookmarks have separate document identities.

## Verification

- Full Python suite: **1901 passed, 3 skipped**. This run preceded the final host identity fixes and the completed browser integration wrapper.
- Relevant desktop/navigation/update suite after review fixes: **80 passed, 1 skipped**.
- Final real-route browser integration after the host identity fix: **3 passed**, including bookmarked/knowledge document restoration after host reload. Existing workspace navigation guard and interaction motion browser checks also passed.
- Independent review findings were fixed: explicit settings query handling, timeout retry, first-visit reauthentication, cross-page refresh and history identity after host reload. Focused final review found no remaining blocker.
- Packaged native WebView2: persistent documents, search input, reading detail, source-message drilldown, settings query and reduced motion passed; **no page script errors**.
- Release verification compared **120 frozen modules** and **58 templates** with source; the **2194-file manifest** passed verification.

Evidence is retained outside Git at `D:/Cache/QQDigestDesktopNavigation-20261030`, including XML test reports, package verification, native test output and screenshots. The trial package is `D:/Apps/QQDigestDesktop-2026-10-30-Navigation-Release`, backend ID `2026-10-30-desktop-navigation`.

The existing idle desktop updater successfully activated this release and updated `D:/Desktop/QQ Digest.lnk`. The live backend reports the new ID, the configuration file checksum is unchanged, and the previous `QQDigestDesktop-2026-10-30-SummaryCompleteness-Release` remains registered for rollback. Package verification passed again after activation. Final relevant precommit tests are recorded in `pytest-precommit.xml`.

## Practical limits

First visits still load their document. Returning to a retained document avoids document reload; data can refresh through the page's existing loader after a relevant mutation. Retained pages consume memory for the duration of the desktop session. These checks verify navigation and state retention, not a measured frame-rate guarantee on every device.
