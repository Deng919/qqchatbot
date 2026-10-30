# Records sidebar entry

- Replaced the sidebar feature-switch shortcut with “运行记录” linking to `/overview`.
- Removed the duplicate footer entry and added a clock icon matching the existing icon system.
- Overview and failure records select the records entry; feature settings select Settings and remain available in its category navigation.
- Updated both initial server markup and navigation rebuilt by the browser, preserving persistent desktop navigation.

## Verification

- Updated navigation expectations failed before implementation, then passed after the change.
- Targeted pytest: 25 passed, with eight independent Chromium browser scenarios covering the new records link, icon, selection, absence of duplicates and feature settings access.
- Packaged native WebView confirms the records shortcut opens the records page, the sliding highlight aligns with it, the footer link is absent and feature settings remain usable.
- Package contents match source: 120 Python modules, 62 templates, 2198 manifest files.
- Release: `D:\Apps\QQDigestDesktop-2026-10-30-RecordsNavigation-Release`; backend `2026-10-30-records-navigation`.
- Evidence: `D:\Cache\QQDigestRecordsNavigation-20261030`.
- Native updater activation preserves the configuration checksum and points `D:\Desktop\QQ Digest.lnk` at the new release, retaining the previous version for rollback.
