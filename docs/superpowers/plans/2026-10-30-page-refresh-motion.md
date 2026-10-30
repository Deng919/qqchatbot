# Page refresh motion implementation plan

> Execute inline with executing-plans; request one focused review before packaging.

**Goal:** Every application page presents refreshed content with a short coherent transition, without blank loading replacements or document reloads during same-document history traversal.
**Architecture:** Keep existing desktop persistent-page transitions and loaders. Add one shared observer for explicitly marked result regions; batch synchronous rendering, animate changed visible content only, skip identical polling and hidden pages, and respect reduced motion. Preserve existing DOM focus, scroll and deck-specific animations. Retain previous search/summary results until new data arrives.
**Tech Stack:** Jinja templates, native MutationObserver and Web Animations API; no dependencies.
**Spec:** Current user request and existing approved desktop/deck motion. Use subtle opacity .78→1 and 4px→0 translation for data refresh, no white borders or full-page fading.

## Constraints / review focus

- Do not replay animation on unchanged polling, typing, image completion or deck disclosure.
- Newest response wins; failures retain useful prior results and provide retry.
- Reduced motion or hidden desktop pages create no continuing animation.
- No reload for summary same-document Back/Forward; preserve scope, scroll and selected point.
- Keep uncommitted unrelated files and private data out of the commit.
- Work in the current authorized feature branch; do not relocate the user's live checkout.

## Steps

- [x] Add browser regressions for changed/identical refresh, delayed search/summary replacement, rapid results, reduced motion and history timeOrigin. Observe failures.
- [x] Add shared refresh motion and mark result regions across every shipped page. Fix early clearing and the same-document reload fallback.
- [x] Verify page inventory and real interactions, existing reading/navigation tests, narrow layout and independent native WebView2. Review the full patch.
- [x] Build/register and safely activate a separate release; preserve rollback.

Delivery: commit/push the verified patch on the current feature branch and verify the remote SHA.

## Ledger

- Ruling: animate only declared data regions, not every DOM mutation — protects active forms, clocks and existing stack animations.
- Ruling: use a small compositor transition without inert content copies — data refresh stays synchronous and does not introduce duplicate controls, focus or accessibility trees.

- Evidence: refresh browser regression first failed because the shared controller was absent. It now passes delayed search/summary retention, failed search retention, real opacity animation, unchanged polling, reduced motion, viewport and same-document history.
- Repair: history restoration no longer gets a fresh script state from reload; reset summaryDetailActive explicitly before reinitializing the restored scope. Existing folder-history regression caught and now covers this.
- Fixture update: wait for the separate result-entrance animation before measuring deck cover anchoring; scope note selection to the actual drawer instead of inert flight copies.
- Final evidence: 62 targeted tests passed; all 17 inventory routes passed in Chromium and packaged native WebView2; reviewer rechecked failure retention and inactive animation cancellation. Package/source verification found no mismatches.
- Activation: RefreshMotion-Release is current, NoticeStyle-Release is the rollback version, configuration checksum is unchanged and the desktop shortcut points to the new executable. See the matching verification document and D:\Cache\QQDigestPageRefresh-20261030.
