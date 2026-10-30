# Knowledge collection filter layout

## Reported problem and changes

Opening the source-group selector in the knowledge collection expanded a grid cell in normal flow. The grid's bottom alignment placed the search, category and submit controls at the bottom of the expanded row, leaving a large empty area above them.

- Opted the knowledge group filter into the existing floating selector. Opening the selector no longer changes the form height or pushes the results down.
- Aligned labels and unified control heights at 42px. Desktop filters occupy one row, intermediate windows use two rows, and narrow windows wrap the controls.
- Restored flex layout and normal label weight inside floating selectors so the general field-label styles cannot collapse the group name and group number together. Long names wrap independently; numbers remain in their own column.
- Reused outside-click and Escape dismissal. The existing inline summary picker remains inline.

## Verification

- New browser regression failed on the original code because opening the group picker enlarged the toolbar.
- `knowledge_layout_browser.cjs` now passes in independent Chromium. It checks form and result positions, separate long-name/9-digit-number columns, checkbox hit testing, submitted filters, Escape and outside dismissal, 390px overflow and console errors.
- Targeted pytest: 102 passed, 0 failures/errors/skips, including eight browser scenarios in the desktop navigation runner.
- Packaged native WebView checks pass: knowledge filters and results keep their positions, group choices are clickable, Escape closes the panel, sidebar selection is correct, and existing persistent navigation flows pass. No page errors.
- Fresh read-only review found no material issues in the scoped CSS or floating selector integration.
- Verified the release manifest and source equivalence for 120 Python modules and 62 templates (2198 manifest files).

## Release locations

- Release: `D:\Apps\QQDigestDesktop-2026-10-30-KnowledgeLayout-Release`
- Backend: `2026-10-30-knowledge-layout`
- Screenshots, tests and activation evidence: `D:\Cache\QQDigestKnowledgeLayout-20261030`
- Shortcut: `D:\Desktop\QQ Digest.lnk`
- Updated through the native updater, retaining the previous release for rollback and preserving the configuration checksum.
