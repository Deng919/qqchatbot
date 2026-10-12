"""Archive table creation and compatibility upgrades."""

import sqlite3


def initialize_archive_schema(connection: sqlite3.Connection) -> None:
    """Initialize schema using the archive's configured sqlite3.Row connection.

    The caller owns transaction completion. This function preserves the existing
    executescript behavior and performs no additional commits or rollbacks.
    """
    connection.executescript(
        """
                CREATE TABLE IF NOT EXISTS feature_settings (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    revision INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL DEFAULT '{}'
                );
                INSERT OR IGNORE INTO feature_settings(singleton) VALUES(1);
                CREATE TABLE IF NOT EXISTS failure_ignores (
                    source_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    ignored_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookmarks (
                    bookmark_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK(kind IN ('message','report','resource','knowledge')),
                    group_id INTEGER NOT NULL,
                    snapshot TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed')),
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bookmarks_status ON bookmarks(status,bookmark_id);
                CREATE TABLE IF NOT EXISTS groups (
                    group_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    daily_summary INTEGER NOT NULL DEFAULT 1,
                    template TEXT NOT NULL DEFAULT 'concise',
                    keywords TEXT NOT NULL DEFAULT '[]',
                    important_candidates INTEGER NOT NULL DEFAULT 1,
                    collection_window_days INTEGER NOT NULL DEFAULT 30,
                    category TEXT NOT NULL DEFAULT 'general'
                );
                CREATE TABLE IF NOT EXISTS seeded_groups (
                    group_id INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS messages (
                    msg_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL,
                    sender_qq INTEGER,
                    timestamp TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    raw_digest TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, msg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_group_time
                    ON messages(group_id, timestamp);
                CREATE TABLE IF NOT EXISTS message_attachments (
                    group_id INTEGER NOT NULL,
                    msg_id TEXT NOT NULL,
                    attachment_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_md5 TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(group_id,msg_id,attachment_key),
                    FOREIGN KEY(group_id,msg_id) REFERENCES messages(group_id,msg_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS message_ingest_sequence (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    high_water INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO message_ingest_sequence(singleton,high_water)
                    SELECT 1,COALESCE(MAX(rowid),0) FROM messages;
                UPDATE message_ingest_sequence SET high_water=MAX(high_water,
                    (SELECT COALESCE(MAX(rowid),0) FROM messages)) WHERE singleton=1;
                CREATE TABLE IF NOT EXISTS sync_state (
                    group_id INTEGER PRIMARY KEY,
                    last_timestamp TEXT,
                    backfill_completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    last_success_at TEXT
                );
                CREATE TABLE IF NOT EXISTS reports (
                    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    report_date TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    candidate_ids TEXT NOT NULL DEFAULT '[]',
                    effective_template TEXT NOT NULL DEFAULT '',
                    input_fingerprint TEXT NOT NULL DEFAULT '',
                    source_message_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(group_id, report_date)
                );
                CREATE TABLE IF NOT EXISTS manual_reports (
                    manual_report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    detail_mode TEXT NOT NULL,
                    effective_template TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    candidate_ids TEXT NOT NULL DEFAULT '[]',
                    input_fingerprint TEXT NOT NULL,
                    source_message_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(group_id, start_date, end_date, detail_mode)
                );
                CREATE INDEX IF NOT EXISTS idx_manual_reports_window
                    ON manual_reports(end_date DESC, start_date DESC, group_id);
                CREATE TABLE IF NOT EXISTS report_revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_kind TEXT NOT NULL CHECK(report_kind IN ('daily','range')),
                    report_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    markdown TEXT,
                    payload TEXT,
                    origin TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(report_kind,report_id,version)
                );
                CREATE TABLE IF NOT EXISTS report_corrections (
                    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revision_id INTEGER NOT NULL REFERENCES report_revisions(revision_id) ON DELETE CASCADE,
                    category TEXT NOT NULL CHECK(category IN ('missing','error','noise')),
                    excerpt TEXT NOT NULL,
                    correction TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open','resolved')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tracked_topics (
                    topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    match_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'tracking' CHECK(status IN ('tracking','archived')),
                    revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_tracked_topics_match ON tracked_topics(match_key);
                CREATE TABLE IF NOT EXISTS tracked_topic_aliases (
                    match_key TEXT PRIMARY KEY,
                    topic_id INTEGER NOT NULL REFERENCES tracked_topics(topic_id)
                );
                CREATE TABLE IF NOT EXISTS topic_discussions (
                    discussion_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity TEXT NOT NULL UNIQUE,
                    topic_id INTEGER NOT NULL REFERENCES tracked_topics(topic_id),
                    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                    report_kind TEXT NOT NULL CHECK(report_kind IN ('daily','range')),
                    report_id INTEGER NOT NULL,
                    report_version INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    available INTEGER NOT NULL DEFAULT 1,
                    link_mode TEXT NOT NULL DEFAULT 'auto' CHECK(link_mode IN ('auto','manual')),
                    revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_topic_discussions_topic_date
                    ON topic_discussions(topic_id,end_date DESC,discussion_id DESC);
                CREATE INDEX IF NOT EXISTS idx_topic_discussions_report
                    ON topic_discussions(report_kind,report_id);
                CREATE INDEX IF NOT EXISTS idx_topic_discussions_group
                    ON topic_discussions(group_id);
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    message_ids TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    candidate_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    content TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    ignore_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_status
                    ON candidates(status, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    item_id TEXT PRIMARY KEY,
                    candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id),
                    markdown_path TEXT NOT NULL,
                    written_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS send_log (
                    send_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER REFERENCES reports(report_id),
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    attempted_at TEXT NOT NULL,
                    recipient TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    manual_retry_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    target_date TEXT,
                    retry_of_job_id INTEGER REFERENCES jobs(job_id)
                );
                CREATE TABLE IF NOT EXISTS job_group_results (
                    job_id INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    group_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('success','skipped','failed')),
                    PRIMARY KEY(job_id, group_id)
                );
                CREATE INDEX IF NOT EXISTS idx_job_group_results_group
                    ON job_group_results(group_id, job_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_status_id
                    ON jobs(status, job_id);
                CREATE INDEX IF NOT EXISTS idx_send_log_active_failures
                    ON send_log(send_id DESC)
                    WHERE status='failed' OR
                          (status IN ('pending_send','sending') AND error<>'');
                CREATE TABLE IF NOT EXISTS catchup_state (
                    state_id INTEGER PRIMARY KEY CHECK (state_id=1),
                    last_viewed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history_inspection_settings (
                    settings_id INTEGER PRIMARY KEY CHECK(settings_id=1),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    lookback_days INTEGER NOT NULL DEFAULT 7 CHECK(lookback_days BETWEEN 1 AND 31),
                    interval_hours INTEGER NOT NULL DEFAULT 24 CHECK(interval_hours BETWEEN 1 AND 168)
                );
                INSERT OR IGNORE INTO history_inspection_settings(settings_id) VALUES(1);
                CREATE TABLE IF NOT EXISTS history_inspection_groups (
                    group_id INTEGER PRIMARY KEY REFERENCES groups(group_id) ON DELETE CASCADE,
                    last_job_id INTEGER NOT NULL REFERENCES jobs(job_id),
                    status TEXT NOT NULL CHECK(status IN ('success','failed')),
                    checked_at TEXT NOT NULL,
                    last_success_at TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    source_count INTEGER,
                    missing_days TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS catchup_reads (
                    item_key TEXT PRIMARY KEY,
                    read_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','completed','canceled')),
                    revision INTEGER NOT NULL DEFAULT 0,
                    source_ids TEXT NOT NULL,
                    primary_source_id TEXT NOT NULL,
                    source_report_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (group_id, primary_source_id)
                        REFERENCES messages(group_id, msg_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status_due
                    ON tasks(status, due_date, task_id);
                CREATE TABLE IF NOT EXISTS task_suggestion_decisions (
                    suggestion_key TEXT PRIMARY KEY,
                    group_id INTEGER NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('confirmed','ignored')),
                    task_id INTEGER REFERENCES tasks(task_id) ON DELETE CASCADE,
                    decided_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminder_rules (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    values_json TEXT NOT NULL,
                    message_cursor INTEGER NOT NULL DEFAULT 0,
                    candidate_cursor INTEGER NOT NULL DEFAULT 0,
                    failure_baseline TEXT NOT NULL,
                    failure_seen TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS reminder_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER NOT NULL REFERENCES reminder_rules(rule_id),
                    event_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    group_id INTEGER REFERENCES groups(group_id) ON DELETE CASCADE,
                    group_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    read INTEGER NOT NULL DEFAULT 0,
                    in_app_status TEXT NOT NULL,
                    windows_status TEXT NOT NULL,
                    windows_attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    delivery_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(rule_id,event_key)
                );
                CREATE INDEX IF NOT EXISTS idx_reminder_events_visibility
                    ON reminder_events(in_app_status,read,event_id DESC);
                CREATE INDEX IF NOT EXISTS idx_reminder_events_windows
                    ON reminder_events(windows_status,next_attempt_at,event_id);
                CREATE TABLE IF NOT EXISTS reminder_resource_baselines (
                    rule_id INTEGER NOT NULL REFERENCES reminder_rules(rule_id),
                    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                    link TEXT NOT NULL,
                    PRIMARY KEY(rule_id,group_id,link)
                );
                """
    )
    task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
    if "revision" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    # Migration: add category column for existing databases
    try:
        connection.execute(
            "ALTER TABLE groups ADD COLUMN category TEXT NOT NULL DEFAULT 'general'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    for statement in (
        "ALTER TABLE groups ADD COLUMN read_images INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE groups ADD COLUMN read_files INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE reports ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE reports ADD COLUMN source_message_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE reports ADD COLUMN effective_template TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE send_log ADD COLUMN recipient TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE send_log ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE send_log ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE send_log ADD COLUMN manual_retry_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE send_log ADD COLUMN next_attempt_at TEXT",
        "ALTER TABLE send_log ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE sync_state ADD COLUMN error TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE sync_state ADD COLUMN last_success_at TEXT",
        "ALTER TABLE jobs ADD COLUMN target_date TEXT",
        "ALTER TABLE jobs ADD COLUMN retry_of_job_id INTEGER REFERENCES jobs(job_id)",
    ):
        try:
            connection.execute(statement)
        except sqlite3.OperationalError:
            pass
    connection.execute(
        """UPDATE sync_state
                   SET last_success_at=CASE
                       WHEN status='active' THEN updated_at
                       ELSE last_timestamp
                   END
                   WHERE last_success_at IS NULL AND last_timestamp IS NOT NULL"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_retry_of ON jobs(retry_of_job_id, job_id)"
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_send_log_recovered_failures
                   ON send_log(send_id DESC)
                   WHERE status='success' AND (retry_count>0 OR manual_retry_count>0)"""
    )
