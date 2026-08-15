"""
Одноразовый скрипт переноса данных из SQLite (data/bot_database.db)
в PostgreSQL.

Запускать ОДИН РАЗ, когда оба контейнера (бот и сайт) уже остановлены,
а PostgreSQL поднят и пуст.

Использование:
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
    POSTGRES_USER=vapeneon POSTGRES_PASSWORD=... POSTGRES_DB=vapeneon \
    SQLITE_PATH=./data/bot_database.db \
    python migrate_to_postgres.py
"""

import os
import sqlite3
import psycopg2

SQLITE_PATH = os.getenv("SQLITE_PATH", "data/bot_database.db")

PG_CONF = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    user=os.getenv("POSTGRES_USER", "vapeneon"),
    password=os.getenv("POSTGRES_PASSWORD"),
    dbname=os.getenv("POSTGRES_DB", "vapeneon"),
)

# ─── Схема Postgres ────────────────────────────────────────────────────────
# Перенесена 1-в-1 из init_db() в main.py и web.py, с заменой:
#   INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL / BIGSERIAL PRIMARY KEY
#   BOOLEAN DEFAULT TRUE/FALSE        -> без изменений (Postgres поддерживает нативно)
#   TIMESTAMP DEFAULT CURRENT_TIMESTAMP -> без изменений

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS warns (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    reason TEXT,
    issued_by BIGINT NOT NULL,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mutes (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    reason TEXT,
    issued_by BIGINT NOT NULL,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS bans (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    reason TEXT,
    issued_by BIGINT NOT NULL,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS admin_warns (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    reason TEXT,
    issued_by BIGINT NOT NULL,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    added_by BIGINT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    role TEXT DEFAULT 'moderator',
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS user_ads (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    message_text TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ad_limit_violations (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    violation_date DATE NOT NULL,
    violation_count INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS donations (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount REAL,
    currency TEXT DEFAULT 'RUB',
    message TEXT,
    donated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_anonymous BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS admin_complaints (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    admin_username TEXT NOT NULL,
    description TEXT NOT NULL,
    complaint_text TEXT NOT NULL,
    evidence TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending',
    handled_by BIGINT,
    handling_result TEXT,
    handled_at TIMESTAMP,
    complaint_type TEXT DEFAULT 'other',
    admin_comment TEXT,
    submitter_tg_id BIGINT DEFAULT 0,
    submitter_username TEXT
);

CREATE TABLE IF NOT EXISTS bot_blocks (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    blocked_by BIGINT NOT NULL,
    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS bot_warns (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    issued_by BIGINT NOT NULL,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS user_reviews (
    id SERIAL PRIMARY KEY,
    from_user_id BIGINT NOT NULL,
    to_user_id BIGINT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS safe_deals (
    id TEXT PRIMARY KEY,
    creator_id BIGINT,
    creator_role TEXT,
    buyer_id BIGINT,
    seller_id BIGINT,
    buyer_username TEXT,
    seller_username TEXT,
    amount REAL,
    description TEXT,
    deadline_days INTEGER,
    created_at TIMESTAMP,
    status TEXT DEFAULT 'created',
    buyer_confirmed BOOLEAN DEFAULT FALSE,
    seller_confirmed BOOLEAN DEFAULT FALSE,
    payment_confirmed BOOLEAN DEFAULT FALSE,
    payment_url TEXT,
    total_amount REAL,
    guarantor_fee REAL,
    group_link TEXT,
    buyer_reviewed BOOLEAN DEFAULT FALSE,
    seller_reviewed BOOLEAN DEFAULT FALSE,
    group_chat_id BIGINT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS safe_deal_reviews (
    id SERIAL PRIMARY KEY,
    deal_id TEXT,
    reviewer_id BIGINT,
    reviewed_user_id BIGINT,
    review_text TEXT,
    rating INTEGER,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS safe_deal_balances (
    user_id BIGINT PRIMARY KEY,
    balance REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS safe_deal_withdrawals (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    amount REAL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP,
    wallet TEXT
);

CREATE TABLE IF NOT EXISTS safe_deal_service_reviews (
    id SERIAL PRIMARY KEY,
    reviewer_id BIGINT,
    review_text TEXT,
    rating INTEGER,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS periodic_messages (
    id SERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bot_users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bot_users_username ON bot_users(username);

CREATE TABLE IF NOT EXISTS tos_accepted (
    user_id BIGINT PRIMARY KEY,
    accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shop_products (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    photo_file_id TEXT,
    added_by BIGINT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS shop_product_photos (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES shop_products(id),
    file_id TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admin_quest_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    is_open BOOLEAN DEFAULT FALSE,
    max_applications INTEGER,
    applications_count INTEGER DEFAULT 0
);

-- индексы (main.py)
CREATE INDEX IF NOT EXISTS idx_warns_user_chat ON warns(user_id, chat_id);
CREATE INDEX IF NOT EXISTS idx_warns_expires ON warns(expires_at);
CREATE INDEX IF NOT EXISTS idx_mutes_user_chat ON mutes(user_id, chat_id);
CREATE INDEX IF NOT EXISTS idx_mutes_expires ON mutes(expires_at);
CREATE INDEX IF NOT EXISTS idx_bans_user_chat ON bans(user_id, chat_id);
CREATE INDEX IF NOT EXISTS idx_bans_expires ON bans(expires_at);
CREATE INDEX IF NOT EXISTS idx_user_ads_user_date ON user_ads(user_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_admin_complaints_status ON admin_complaints(status);
CREATE INDEX IF NOT EXISTS idx_admin_complaints_user ON admin_complaints(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_complaints_created ON admin_complaints(created_at);
CREATE INDEX IF NOT EXISTS idx_bot_blocks_user ON bot_blocks(user_id);
CREATE INDEX IF NOT EXISTS idx_bot_blocks_active ON bot_blocks(is_active);
CREATE INDEX IF NOT EXISTS idx_bot_warns_user ON bot_warns(user_id);
CREATE INDEX IF NOT EXISTS idx_bot_warns_active ON bot_warns(is_active);
CREATE INDEX IF NOT EXISTS idx_user_reviews_to_user ON user_reviews(to_user_id);
CREATE INDEX IF NOT EXISTS idx_user_reviews_from_user ON user_reviews(from_user_id);
CREATE INDEX IF NOT EXISTS idx_safe_deals_buyer ON safe_deals(buyer_id);
CREATE INDEX IF NOT EXISTS idx_safe_deals_seller ON safe_deals(seller_id);
CREATE INDEX IF NOT EXISTS idx_safe_deals_status ON safe_deals(status);

-- таблицы web.py
CREATE TABLE IF NOT EXISTS user_reports (
    id SERIAL PRIMARY KEY,
    reporter_id BIGINT NOT NULL,
    reporter_username TEXT,
    reported_id BIGINT NOT NULL,
    reported_username TEXT,
    reason TEXT,
    message_text TEXT,
    message_photo TEXT,
    message_link TEXT,
    chat_id BIGINT NOT NULL,
    status TEXT DEFAULT 'pending',
    handled_by TEXT,
    handled_action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    handled_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS site_admins (
    id SERIAL PRIMARY KEY,
    tg_id BIGINT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    added_by BIGINT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    can_review_admin_complaints INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS site_bans (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    tg_id BIGINT DEFAULT 0,
    reason TEXT,
    issued_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS site_warns (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    tg_id BIGINT DEFAULT 0,
    reason TEXT,
    issued_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    can_review_admin_complaints INTEGER DEFAULT 0,
    expires_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    tg_id BIGINT DEFAULT 0,
    appeal_reason TEXT DEFAULT '',
    appeal_type TEXT DEFAULT '',
    expires_at TIMESTAMP NOT NULL
);
"""

# Порядок важен из-за FOREIGN KEY (shop_product_photos -> shop_products)
TABLES_IN_ORDER = [
    "warns", "mutes", "bans", "admin_warns", "admins", "user_ads",
    "ad_limit_violations", "donations", "admin_complaints", "bot_blocks",
    "bot_warns", "user_reviews", "safe_deals", "safe_deal_reviews",
    "safe_deal_balances", "safe_deal_withdrawals", "safe_deal_service_reviews",
    "periodic_messages", "bot_users", "tos_accepted",
    "shop_products", "shop_product_photos", "admin_quest_state",
    "user_reports", "site_admins", "site_bans", "site_warns",
    "admin_sessions", "user_sessions",
]

# Колонки типа BOOLEAN в SQLite хранятся как 0/1 — их нужно приводить к bool
BOOL_COLUMNS = {
    "mutes": {"is_active"},
    "bans": {"is_active"},
    "admin_warns": {"is_active"},
    "donations": {"is_anonymous"},
    "bot_blocks": {"is_active"},
    "bot_warns": {"is_active"},
    "safe_deals": {"buyer_confirmed", "seller_confirmed", "payment_confirmed",
                   "buyer_reviewed", "seller_reviewed"},
    "admin_quest_state": {"is_open"},
}


def migrate():
    if not PG_CONF["password"]:
        raise SystemExit("Задайте POSTGRES_PASSWORD в переменных окружения")
    if not os.path.exists(SQLITE_PATH):
        raise SystemExit(f"Не найден файл SQLite: {SQLITE_PATH}")

    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row

    pconn = psycopg2.connect(**PG_CONF)
    pconn.autocommit = False
    pcur = pconn.cursor()

    print("Создаю схему в Postgres...")
    pcur.execute(SCHEMA_SQL)
    pconn.commit()

    for table in TABLES_IN_ORDER:
        scur = sconn.execute(f"SELECT * FROM {table}")
        rows = scur.fetchall()
        if not rows:
            print(f"  {table}: нет данных, пропускаю")
            continue

        cols = rows[0].keys()
        bool_cols = BOOL_COLUMNS.get(table, set())
        placeholders = ",".join(["%s"] * len(cols))
        col_list = ",".join(cols)
        insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

        values = []
        for r in rows:
            row_vals = []
            for c in cols:
                v = r[c]
                if c in bool_cols and v is not None:
                    v = bool(v)
                row_vals.append(v)
            values.append(tuple(row_vals))

        pcur.executemany(insert_sql, values)
        pconn.commit()
        print(f"  {table}: перенесено {len(rows)} строк")

    # Синхронизируем автоинкремент-последовательности с максимальным id,
    # иначе следующий INSERT в Postgres может попытаться вставить уже занятый id.
    for table in TABLES_IN_ORDER:
        pcur.execute(f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = %s AND column_default LIKE 'nextval%%'
        """, (table,))
        row = pcur.fetchone()
        if not row:
            continue
        id_col = row[0]
        pcur.execute(f"SELECT setval(pg_get_serial_sequence(%s, %s), COALESCE((SELECT MAX({id_col}) FROM {table}), 1), true)",
                     (table, id_col))
    pconn.commit()

    sconn.close()
    pcur.close()
    pconn.close()
    print("Готово. Данные перенесены в Postgres.")


if __name__ == "__main__":
    migrate()
