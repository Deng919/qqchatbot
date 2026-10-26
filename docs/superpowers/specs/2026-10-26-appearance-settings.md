# Appearance settings

Appearance selection now lives in Settings → Appearance. The sidebar and login page no longer contain a theme selector. Both retain the existing before-paint theme initialization and saved preference, including the default dark appearance.

The settings section uses three native radio choices with small interface previews: light, night and system. Selecting a choice applies and saves it immediately. Checked, hover and keyboard-focus states use the existing theme colors; night mode retains its neutral gray palette. Options form three columns on desktop and stacked rows on narrow screens. The separate extension category is now labeled 功能开关.

The existing local-storage key and cross-window/system preference behavior are preserved. Theme changes do not submit unrelated forms or discard their drafts. No new dependencies, backend APIs, settings state or save button were added.

Verification: all 61 frontend scripts and 136 related Python tests passed. Independent review found no important issues. Source-browser and isolated packaged-desktop checks covered the actual settings category, radio keyboard operation, system changes, saved choices across reload/navigation, absence of controls from the sidebar/reading page, and preservation of an unrelated AI settings draft. Light and dark screenshots at widths 1440, 390 and 320 showed no horizontal overflow; browser page errors were zero. Native focus checks account for Windows DPI rounding.

Package verification matched 119 frozen Python modules, 56 templates and 2,192 manifest entries; the executable is unchanged. Evidence is in `D:\Cache\QQDigestAppearanceSettings-20261026`. The release is `D:\Apps\QQDigestDesktop-2026-10-26-AppearanceSettings-Release`; the preceding AutoRead release remains available for rollback.
