# Candidate filter layout and navigation verification

## Fixes

- Removed both unconditional candidate card left emphasis borders. Unselected cards now use the same border on all four sides.
- Extension pages select their own sidebar entry instead of also selecting a primary entry. Settings section changes update the primary state without reloading.
- Candidate filter controls align to the top of the grid. Its group selector opens as an overlay, so expanding the group list does not stretch neighboring controls.
- The overlay closes on outside click, Escape, or resize and stays within the horizontal viewport. Escape restores focus to the summary.
- Floating selection is explicitly enabled only for the candidate filter. Other selectors retain their inline layout, including the summary filter inside its scrolling container.

## Evidence

- Regression reproduced before the border fix: left border was 3px while right border was 1px.
- Review found that enabling the floating selector globally clipped the summary picker. Added a failing `/reports` regression before limiting the behavior to the candidate page; the regression then passed.
- Targeted pytest run: 72 passed, 0 failed, 0 skipped. Includes seven independent Chromium browser scenarios through `test_desktop_navigation.py`.
- `candidate_visual_browser.cjs` checks card borders, filter height, outside/Escape dismissal, 390px placement, single sidebar selection, dynamic settings selection, summary picker search, and page errors.
- Packaged native WebView verification checks candidate borders, dropdown height, the sliding sidebar highlight position, persistent page documents, retained search and reading state, source drilldown, settings query navigation, and reduced motion. No page errors.
- Fresh review accepted the scoped selector fix with no remaining material findings.
- Package verification: 120 Python modules and 62 templates match source; release manifest contains 2198 files.

## Release and local evidence

- Release: `D:\Apps\QQDigestDesktop-2026-10-30-CandidateLayout-Release`
- Backend: `2026-10-30-candidate-layout`
- Evidence: `D:\Cache\QQDigestCandidateLayout-20261030`
- Desktop shortcut: `D:\Desktop\QQ Digest.lnk`
- Activated through the native updater; configuration checksum preserved and previous release retained for rollback.

Verification covers the reported layout and selection regressions and the listed navigation flows; it is not a claim that every application behavior is defect-free.
