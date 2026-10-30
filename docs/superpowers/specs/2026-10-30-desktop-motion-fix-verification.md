# Desktop motion corrections — 2026-10-30

## User-visible changes

- Followed groups fade and translate on desktop navigation. An inert, ID-stripped exit copy lets the old section finish its animation inside the incoming sidebar. Rapid reversal starts from the current displayed opacity and position; the transformed position is not counted twice. The copy retains the list's bounded height and scroll position and is removed after the transition. Reduced motion skips this animation.
- Dashboard group cards retain their original column and cover height. Their content opens and closes downward through an animated details-content height. One ResizeObserver tracks lazy-loaded content and is disconnected when the dashboard is replaced. Native details keyboard behavior is preserved; browsers without the details-content selector retain immediate native disclosure. The outer list reserves scrollbar space so classic Windows scrollbars do not change column widths on expansion.
- Selected secondhand categories retain their background and text emphasis, without the inset left stripe.

## Verification

`tests/desktop_detail_motion.cjs` reproduced all three original failures before the changes. It also caught the rapid-reversal double translation before that correction. The final browser test covers the second and third card's original coordinates, intermediate opening/closing heights, uncropped loaded content, sidebar enter/exit and interrupted movement, scroll retention, keyboard disclosure, reduced motion, narrow layout, and category styling. It runs with the existing real-route desktop integration test.

The final relevant Python test run passed **44 tests**, including real-route browser integration. Existing dashboard UI/layout, shared interaction motion and summary scroll scripts also passed. Native WebView2 passed the same motion cases and the existing retained-reading/search, source-message panel and settings checks with **no page script errors**. Native testing exposed and verified the classic-scrollbar width correction. The focused reviewer verified the changes and found no remaining material issue.

Packaging verified **120 frozen modules**, **58 templates** and a **2194-file manifest** against source. The isolated evidence, test reports and screenshots are at `D:/Cache/QQDigestDesktopMotionFix-20261030`. The release is `D:/Apps/QQDigestDesktop-2026-10-30-MotionFix-Release`, backend `2026-10-30-desktop-motion-fix`.

The existing idle updater successfully activated the release. The live backend reports the new ID, `D:/Desktop/QQ Digest.lnk` points to the new executable, the configuration checksum is unchanged, and `QQDigestDesktop-2026-10-30-Navigation-Release` is retained as the rollback version. Manifest verification passed after switching.

The existing timeout regression now accelerates only the intended first destination load after the initial host is ready; ordinary startup and the successful retry keep their normal timeout. This prevents unrelated startup load from invalidating the timeout test under concurrent native testing.
