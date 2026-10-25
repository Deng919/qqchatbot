# Folder Navigation Implementation Plan

> Execute inline using executing-plans; review the final change once.

**Goal:** Add upper back/forward controls with folder-like history for pages and summary browsing.

**Architecture:** Use Chromium/WebView native session history, without a second navigation stack. Store the existing summary reading snapshot per history entry; reload same-document traversals to restore that entry. Keep startup/login/API paths outside the toolbar history boundary.

**Approved scope:** The user requested upper return controls like folders. Back/forward represent the last visited location; unavailable directions are disabled. Preserve summary group/date/view/page/scroll, support branch truncation, refresh and cross-page navigation.

- [x] Write a real Chromium regression for list → full report → back/forward, filter/view history, refresh, branch, scroll and cross-page boundaries; observe failure.
- [x] Add shared toolbar controls and history integration, preserving existing summary snapshot logic; avoid pushes from startup/background refresh and suppress stale saves during traversal.
- [x] Verify desktop/mobile and packaged WebView, run related regressions, review, update installed release and GitHub work branch.

QA: D:\Cache\QQDigestFolderNavigation-20261025. Release: D:\Apps\QQDigestDesktop-2026-10-25-FolderNavigation-Final-Release.
