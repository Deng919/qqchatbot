# Desktop Navigation Implementation Plan

> **For agentic workers:** Use executing-plans inline for implementation, then request one focused review.

**Goal:** Apply the approved persistent desktop page motion to the real Windows application.
**Architecture:** A desktop-only host retains existing authenticated same-origin documents; a small bridge installs before page scripts to coordinate history and content transforms. Existing ordinary web pages stay usable independently.
**Tech Stack:** FastAPI, Jinja, browser History API, requestAnimationFrame, pywebview; no new dependencies.
**Spec:** docs/superpowers/specs/2026-10-30-desktop-navigation.md

## Global Constraints

Preserve authentication, drafts, filters, scroll and reading state. Never cache arbitrary origins or API/download routes. No white borders or page opacity blending. Reduced motion is immediate. Only desktop startup selects the host. New release and evidence use the paths in the spec.

## Review Focus

- Rapid navigation while a new page is loading must show the final requested page.
- Retained background drafts must survive navigation and still block destructive reload.
- Browser/folder back must restore internal reading states without adding child history entries.
- Native and custom dialogs in inactive frames must not paint or capture focus.
- Logout/auth redirects and external/download links must not expose cached authenticated content.

## Task 1: Persistent host and page bridge

- [x] Add route/auth tests and a browser navigation test; run and observe failures.
- [x] Add desktop_workspace route and host template; register the route and packaged template.
- [x] Install the early page bridge; delegate embedded history while preserving standalone behavior.
- [x] Add summary state restore and switch desktop launch to the host.
- [x] Verify real page revisits, draft and scroll retention, immediate retarget, history, reduced motion, loading races/failures and unsafe navigation.

## Task 2: Package and verify

- [x] Run relevant existing browser checks, then the Python suite.
- [x] Request focused review and fix material findings.
- [x] Build a separate native release and verify its packaged routes/assets in isolated data.
- [x] Commit only source, tests and necessary documentation, push current work branch, and verify remote SHA.
