# Deck spread implementation plan

> **For agentic workers:** Execute inline; request one focused review before packaging.

**Goal:** Integrate the approved pull-out-and-spread demo into desktop summary dashboards.
**Architecture:** Preserve native details covers and existing per-group lazy loading. Move the one active group's existing body into a full-width row immediately after its cover row. A small owned motion controller animates card layout and inert note copies; it also owns blank/Escape and resize cleanup.
**Tech Stack:** Existing Jinja/JavaScript/CSS, Web Animations API; no new dependencies.
**Spec:** The user-approved `D:/Cache/QQDigestMotionMVP-20261030/deck-demo.html`: pull notes from the selected stack, move down, spread across the next full row; reverse on dismissal.

## Constraints and review focus

- Preserve source links, lazy pagination, retry, automatic read and reading return state.
- Second and third covers keep their position while their notes spread below the complete row.
- Rapid selection, delayed loading and filtering never reopen an obsolete group or leave inert copies behind.
- Keyboard disclosure, Escape, blank dismissal, narrow windows and reduced motion remain usable.
- Dashboard refresh restores the active group without replaying the initial pull-out.
- Only owned native test instances are automated. Preserve private data, unrelated files and rollback packages.

## Implementation

- [x] Replace the obsolete same-column browser assertion with approved full-row behavior; observe its failure.
- [x] Add the motion controller, connect existing dashboard bodies/loading, and replace old disclosure-height CSS.
- [x] Verify scoped pagination/read/return behavior, browser motion, filtering, retries and rapid changes. Request focused review and fix material findings.
- [x] Build and verify a separate release at `D:/Apps/QQDigestDesktop-2026-10-30-DeckSpread-Release`, with evidence at `D:/Cache/QQDigestDeckSpread-20261030`.
- [x] Activate through the idle updater; commit source/tests/docs, push the existing work branch and verify its remote SHA.
