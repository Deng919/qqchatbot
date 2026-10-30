# Cockpit only and full archive processing

User scope: remove project ChatGPT bridge and DeepSeek integrations; keep one Cockpit local API configuration for summary, report QA, archive QA, vision, connection test and secondhand. Do not uninstall shared machine services. Reprocess every already archived message, regenerate summaries and rerun secondhand recognition; do not collect additional QQ history. Preserve prior reports/revisions and credentials. Existing group/blocked-sender controls remain effective; explicitly account for any excluded content.

Architecture: use the existing generic AIClient directly with one base_url/model/key configuration. Remove provider chains, bridge subprocesses, provider selection, connection profiles and per-purpose routing. Accept legacy YAML enough to migrate safely, but do not execute legacy providers. Retain usage accounting, atomic config writes, protected credential files, revisions and operation locks.

Bulk work uses a backed-up, resumable local task ledger outside Git. Enumerate all archived group-days from a fixed snapshot. Regenerate old reports and fill missing full-day reports, preserving older versions. For marketplace sources preserve evidence checks, record unavailable attachments and bounded retry failures. Verify final coverage and report actual counts.
