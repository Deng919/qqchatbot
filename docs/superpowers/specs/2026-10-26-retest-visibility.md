# 2026-10-26 installed desktop retest

The follow-up retest found a visibility regression in the shared action-button decoration. Its `display:inline-flex!important` selector was more specific than the base `[hidden]` and `[data-feature-off]` rules, so a decorated button could be rendered even when the page or feature gate hid it. This affected the unused return-to-reading action and could affect disabled-feature actions elsewhere.

The fix gives the existing visibility attributes precedence for decorated buttons. It adds no state or business-logic branches. A real Chromium regression first failed against the original CSS, then passed after the fix. It covers decoration while hidden, revealing the action, and hiding an already decorated action for both attributes.

Verification evidence is stored in `D:\Cache\QQDigestRetest-20261026`. All 60 frontend scripts were rerun after the fix. The isolated native WebView retest exercises hidden decorated buttons, folder-style back/forward, report and point navigation, history filter retention, persisted appearance settings, and neutral night-mode colors. It uses synthetic messages and disables external AI and collection. A second browser walkthrough checks hidden-node rendering explicitly rather than trusting the presence of a `hidden` attribute.

The release is `D:\Apps\QQDigestDesktop-2026-10-26-VisibilityFix-Release`. Package verification compares 119 frozen Python modules, 56 templates and all 2,192 manifest entries against the source. The executable remains unchanged; the correction is in the bundled shared template.

Final results: 1,850 Python tests passed and 3 skipped; all 60 frontend scripts passed; 46 browser states produced 92 desktop/mobile snapshots with zero page errors, overflow, contrast or hidden-rendering findings. The isolated packaged desktop checks passed, including direct assertions that decorated hidden and feature-disabled buttons compute to `display:none`.

The full Python suite was started before this CSS-only correction; the frontend suite, browser walkthrough and native checks were rerun against the correction. Consult the saved JSON results and logs for activation status. The checks establish the exercised flows; they do not imply that every possible interaction is defect-free.
