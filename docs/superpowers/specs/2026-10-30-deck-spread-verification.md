# Desktop summary deck spread verification

## Approved interaction

The active stack keeps its cover in place. Notes pull out, move below the complete source row, then spread across a full-width row. Lower covers make room. Clicking the cover, the return button, blank dashboard space, or Escape returns the notes. A single group remains active.

## Implementation

`summary_deck_motion.html` owns the full-row drawer, cover layout animation and inert note copies. Existing dashboard bodies move between their native details element and the drawer; lazy pagination, retry, source navigation and automatic read reuse their existing data paths. No dependencies or new data storage were added.

## Verification

- Targeted pytest: 74 passed, no skips; final fixture/data-flow rerun: 8 passed.
- Independent Chromium: full-row placement for second/third covers; visible pull-out copies; rapid switching; blank and Escape dismissal; keyboard focus; source filtering; reduced motion; narrow viewport.
- Review findings reproduced before repair: stale multiple expanded flags reopen a dismissed group; resizing from three to two columns splits the source row. Both regressions now pass.
- Reading browser: 90 lazy-loaded points; delayed read saves; inner and outer scroll restored after hidden refresh; complete report and back at desktop/tablet/mobile sizes. The shorter new layout uses the actual clamped scroll offset in the fixture.
- Independent reviewer reran deck motion, reading return and continuous pagination after the fixes; no remaining material findings in scope.
- PyInstaller build succeeded. Packaged/source comparison: 120 modules, 59 templates, zero mismatches; release manifest: 2195 files.
- Owned isolated native WebView2: reading/search documents retain state, original-message drilldown, settings query, reduced motion, deck/resize/dismiss regressions; no page errors. Synthetic archive and separate profile only.

## Release and evidence

- Release: `D:/Apps/QQDigestDesktop-2026-10-30-DeckSpread-Release`
- Backend ID: `2026-10-30-deck-spread`
- Local test/build/native evidence: `D:/Cache/QQDigestDeckSpread-20261030`
- Approved independent demo remains at `D:/Cache/QQDigestMotionMVP-20261030/deck-demo.html`.

## Activation

Official updater transaction succeeded. Running backend ID and release registry confirmed the new version. Desktop shortcut `D:/Desktop/QQ Digest.lnk` targets the new executable; configuration SHA-256 remained unchanged. Rollback points to `D:/Apps/QQDigestDesktop-2026-10-30-MotionFix-Release`.
