"""SQLite 连接与 schema 初始化（readings 环冲 + App 业务库）。

readings 库沿用旧版全局连接（check_same_thread=False），
schema 与原 server.py 完全一致，生产数据无需迁移。
"""

import sqlite3

from petsense import config


def _connect_readings():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL)')
    cur.execute('''CREATE TABLE IF NOT EXISTS action_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        label TEXT NOT NULL,
        action_key TEXT NOT NULL,
        confidence REAL NOT NULL,
        scores_json TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        window_start_ts_ms INTEGER NOT NULL,
        window_end_ts_ms INTEGER NOT NULL,
        model_version TEXT NOT NULL,
        predicted_at TEXT NOT NULL
    )''')
    cur.execute('CREATE INDEX IF NOT EXISTS action_predictions_device_time ON action_predictions(device_id, id DESC)')
    cur.execute('PRAGMA journal_mode=WAL')
    conn.commit()
    return conn, cur


conn, cur = _connect_readings()


def app_connect():
    db = sqlite3.connect(config.APP_DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_app_db():
    with app_connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            avatar_emoji TEXT NOT NULL DEFAULT '🙂',
            bio TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            species TEXT NOT NULL DEFAULT 'dog',
            breed TEXT NOT NULL DEFAULT '',
            avatar_emoji TEXT NOT NULL DEFAULT '🐾',
            device_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pet_id INTEGER,
            image_path TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            location_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(post_id, user_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL,
            followee_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(follower_id, followee_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS daily_stats (
            pet_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            sleep_seconds INTEGER NOT NULL DEFAULT 0,
            active_seconds INTEGER NOT NULL DEFAULT 0,
            hr_sum REAL NOT NULL DEFAULT 0,
            hr_count INTEGER NOT NULL DEFAULT 0,
            samples INTEGER NOT NULL DEFAULT 0,
            UNIQUE(pet_id, date)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS home_layout (
            pet_id INTEGER PRIMARY KEY,
            rooms_json TEXT NOT NULL,
            safe_zone_json TEXT NOT NULL,
            anchors_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )''')

        # 增量列：PetGIF 数字形象绑定 / 头像图片 / 社区外部媒体
        pet_cols = {row[1] for row in db.execute('PRAGMA table_info(pets)')}
        if 'petgif_id' not in pet_cols:
            db.execute('ALTER TABLE pets ADD COLUMN petgif_id TEXT')
        if 'avatar_image' not in pet_cols:
            db.execute('ALTER TABLE pets ADD COLUMN avatar_image TEXT')
        post_cols = {row[1] for row in db.execute('PRAGMA table_info(posts)')}
        if 'media_url' not in post_cols:
            db.execute('ALTER TABLE posts ADD COLUMN media_url TEXT')
        if 'media_kind' not in post_cols:
            db.execute('ALTER TABLE posts ADD COLUMN media_kind TEXT')
