"""Marketplace data lives with the archive and follows its source deletions."""


def initialize(connection):
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS market_groups(
            group_id INTEGER PRIMARY KEY REFERENCES groups(group_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS market_blocks(
            qq INTEGER PRIMARY KEY CHECK(qq>0), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS market_favorites(item_key TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS market_scans(
            source_key TEXT PRIMARY KEY, group_id INTEGER NOT NULL, msg_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            FOREIGN KEY(group_id,msg_id) REFERENCES messages(group_id,msg_id) ON DELETE CASCADE,
            FOREIGN KEY(group_id) REFERENCES market_groups(group_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS market_posts(
            post_key TEXT PRIMARY KEY, item_key TEXT NOT NULL,
            group_id INTEGER NOT NULL, msg_id TEXT NOT NULL, source_key TEXT NOT NULL, path TEXT NOT NULL,
            seller_qq INTEGER, seller_name TEXT NOT NULL, forwarder_qq INTEGER,
            title TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('sale','want')),
            description TEXT NOT NULL, condition_text TEXT NOT NULL, price REAL, price_text TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('unknown','sold','fulfilled')),
            evidence TEXT NOT NULL, published_at TEXT NOT NULL, forwarded_at TEXT,
            incomplete INTEGER NOT NULL DEFAULT 0,original_time_unknown INTEGER NOT NULL DEFAULT 0,
            bundle_key TEXT NOT NULL DEFAULT '', bundle_label TEXT NOT NULL DEFAULT '',
            bundle_mode TEXT NOT NULL DEFAULT 'single', components_json TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY(group_id,msg_id) REFERENCES messages(group_id,msg_id) ON DELETE CASCADE,
            FOREIGN KEY(group_id) REFERENCES market_groups(group_id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_market_item ON market_posts(item_key,published_at);
        CREATE INDEX IF NOT EXISTS idx_market_date ON market_posts(published_at,group_id);
    ''')
    columns={row[1] for row in connection.execute('PRAGMA table_info(market_posts)')}
    if 'original_time_unknown' not in columns:
        connection.execute('ALTER TABLE market_posts ADD COLUMN original_time_unknown INTEGER NOT NULL DEFAULT 0')
    if 'bundle_key' not in columns:
        connection.execute("ALTER TABLE market_posts ADD COLUMN bundle_key TEXT NOT NULL DEFAULT ''")
    if 'bundle_label' not in columns:
        connection.execute("ALTER TABLE market_posts ADD COLUMN bundle_label TEXT NOT NULL DEFAULT ''")
    if 'bundle_mode' not in columns:
        connection.execute("ALTER TABLE market_posts ADD COLUMN bundle_mode TEXT NOT NULL DEFAULT 'single'")
    if 'components_json' not in columns:
        connection.execute("ALTER TABLE market_posts ADD COLUMN components_json TEXT NOT NULL DEFAULT '[]'")
