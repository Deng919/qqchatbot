# QQ Digest Windows Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows EXE that opens the existing QQ Digest interface in its own desktop window.

**Architecture:** Reuse the current FastAPI app in process when no local server is running, and connect to an existing service otherwise. Embed the URL with pywebview and package templates and Python dependencies with PyInstaller.

**Tech Stack:** Python, FastAPI, uvicorn, pywebview, WebView2, PyInstaller, pytest.

---

### Task 1: Desktop service lifecycle

**Files:** Create `qq_digest/desktop.py`; test `tests/test_desktop.py`.

- [ ] Write a failing test for configuration resolution, existing server reuse, and service shutdown.
- [ ] Run `D:\CodexTools\python\Scripts\python.exe -m pytest tests/test_desktop.py -q` and confirm the expected failure.
- [ ] Implement the smallest service controller that satisfies the tests.
- [ ] Run the targeted test again.

### Task 2: Desktop window

**Files:** Modify `qq_digest/desktop.py`, `pyproject.toml`; test `tests/test_desktop.py`.

- [ ] Add a test that checks window creation and cleanup with injected fake GUI functions.
- [ ] Confirm it fails, then implement pywebview startup in the main thread.
- [ ] Verify that the targeted test passes.

### Task 3: Windows package

**Files:** Create `scripts/build-desktop.py`, `qq_digest_desktop.spec`; modify `README.md`.

- [ ] Add `desktop` dependencies and include Jinja templates in the PyInstaller build.
- [ ] Build in `D:\Cache\QQDigestDesktop` and release to `D:\Apps\QQDigestDesktop`.
- [ ] Write `launcher.json` with the path to this machine's current config file.
- [ ] Run the EXE, inspect the window, and confirm the app and login page load.

### Task 4: Verification

- [ ] Run `D:\CodexTools\python\Scripts\python.exe -m pytest -q`.
- [ ] Run `git diff --check`.
- [ ] Confirm the EXE works both with the current local service and after that service is stopped.
