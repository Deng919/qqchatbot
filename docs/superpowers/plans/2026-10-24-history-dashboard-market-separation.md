# History Dashboard and Marketplace Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let historical summaries use the existing dashboard and keep registered marketplace groups out of ordinary summary workflows.

**Architecture:** Reuse `market_groups` membership as the authoritative classification. Keep collection and marketplace records intact. Blank start/end dates represent all historical dates; send an explicit history query to the reading API rather than replacing the range with a recent week when switching views.

**Tech Stack:** Existing Python/FastAPI/SQLite, Jinja templates and plain JavaScript, pytest and independent Chromium/WebView verification.

**Spec:** User request in this conversation: history supports dashboard format; marketplace groups belong to secondhand information and are excluded from ordinary summary selection, generation and equivalent flows.

## Global Constraints

- Use existing marketplace membership; no new group category, migration or automatic naming classifier.
- Collection, raw messages, existing reports and secondhand data remain stored. Central group management retains access to every group.
- Enforce generation exclusion on the server, including explicit stale IDs and automatic daily generation. Do not merely hide frontend options.
- Keep historical ranges across dashboard/group/manage switches, navigation, pagination, expansion and restoration.
- Use `D:\CodexTools\python\Scripts\python.exe`; independent browser tests, never Codex IAB. Keep existing unrelated untracked files.
- Build and validate before delivery; preserve stable rollback and the requested small set of representative local releases. Commit and push the applicable work branch after verification.

## Review Focus

- An explicit or restored marketplace group ID must not bypass ordinary summary exclusion.
- Empty ordinary-group selection must not widen into all groups or marketplace-only content.
- Dashboard group expansion must fetch all historical pages with the same range and classification.
- Existing first-visit baselines, source/read/bookmark identifiers and prior reading restoration must remain stable.
- Secondhand pages, collection and raw search must retain their existing access; disabled groups and renamed marketplace groups remain correctly classified by membership.

## Tasks

- [x] Backend separation: add a reusable summary-eligible group query; scope `/api/groups`; apply it to daily/manual preview, execution, regeneration and ordinary report lists/summary statistics; add failing tests then fixes. Keep generic collection/management APIs unchanged by default.
- [x] History display: support explicit all-date reading queries; preserve blank historical dates through view switches; carry history to grouped expansion; add backend and browser regressions.
- [x] Summary UI scope: use summary-only group inventory in ordinary sidebar, reading and generation selectors; retain all-group inventory for management and raw search; reject stale marketplace selections server-side.
- [x] Integration verification: related Python and CJS suites, independent browser desktop/mobile/history/generation/marketplace flows and isolated packaged startup. Review diff and fix substantive findings.
- [x] Delivery: record results, validate package and current/rollback/entry state, activate verified build without interrupting work, retain representative versions, commit/push and verify remote SHA.
