# Automatic read after opening summary content

Opening a summary point now saves its read state automatically. Listing points, expanding a group, and background rerenders do not mark unseen content read. Restoring an opened point also saves read state without adding an extra navigation entry. The normal manual mark-read action is hidden; marking content unread remains available, with retry offered if persistence fails.

A successfully opened full report saves its exact points using the existing authenticated summary-reading APIs. Pagination reads a stable `all` source and skips already read points; writes are sequential. Closing or changing the report stops unsent writes, preserving later manual unread choices. A quick open/back can therefore mark only the points already processed. Loading a report unsuccessfully does not mark it read.

Pending saves are shared per point. Opposing read/unread intents run in order so explicitly reopening a point during a pending manual unread ends read. Successful saves reconcile the matching point in the current scope; a new opener also receives failures from a shared save and can retry. Background updates preserve the newer selected article, filter and scrolling position, including when the unread list becomes empty.

Validation:

- A new failure-first regression covers automatic opening, duplicate requests, delayed selection, manual unread, retry, report pagination/cancellation, and shared saves across scope changes, including success/failure and opposing intents.
- All 61 frontend scripts passed. The 174 Python tests for summary reading, catch-up state, web routes and report completeness passed. No backend code or API contract changed.
- An isolated copy of the desktop release exercised actual persistence and reload, desktop/mobile layouts, failed report loading, a failed read save followed by retry, and a full report with 103 points spanning two API pages. Another group's point remained unread. There were zero browser page errors.
- Package verification matched 119 frozen Python modules, 56 templates and 2,192 manifest entries. The executable is unchanged.
- Independent review findings about cancellation, stale scope results and opposing intents were reproduced, fixed and covered by regressions; final review found no remaining important issues.

Evidence: `D:\Cache\QQDigestAutoRead-20261026`.
Release: `D:\Apps\QQDigestDesktop-2026-10-26-AutoRead-Release`.
