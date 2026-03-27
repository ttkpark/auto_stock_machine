"""SQLite 데이터베이스 관리.

다중 계정 시스템의 데이터 계층을 담당합니다.
- 스키마 초기화 및 자동 마이그레이션
- 사용자, 설정, 트레이스, 히스토리 CRUD
"""
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path(os.environ.get("DATABASE_PATH", "data/app.db").strip() or "data/app.db")

# .env에서 DB로 이관할 사용자별 키 목록
MANAGED_USER_KEYS = [
    "IS_REAL_TRADING",
    "KIS_MOCK_APP_KEY", "KIS_MOCK_APP_SECRET", "KIS_MOCK_ACCOUNT_NUMBER",
    "KIS_REAL_APP_KEY", "KIS_REAL_APP_SECRET", "KIS_REAL_ACCOUNT_NUMBER",
    "GEMINI_API_KEY", "GEMINI_MODEL_NAME",
    "CLAUDE_API_KEY", "CLAUDE_MODEL_NAME",
    "OPENAI_API_KEY", "OPENAI_MODEL_NAME",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "BUY_BUDGET_RATIO", "MIN_AI_CONSENSUS", "MAX_BUY_STOCKS",
    "TAKE_PROFIT_RATE", "STOP_LOSS_RATE",
    "TRAILING_STOP_ATR_MULTIPLIER", "MARKET_CRASH_THRESHOLD", "STAGNANT_HOLDING_DAYS",
]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_config (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL DEFAULT '',
    UNIQUE(user_id, key)
);

CREATE TABLE IF NOT EXISTS ai_traces (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    run_id       TEXT NOT NULL,
    mode         TEXT NOT NULL,
    trading_mode TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_user_run ON ai_traces(user_id, run_id);
CREATE INDEX IF NOT EXISTS idx_traces_user_time ON ai_traces(user_id, created_at);

CREATE TABLE IF NOT EXISTS action_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    action     TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_user ON action_history(user_id, created_at);

CREATE TABLE IF NOT EXISTS holdings_tracker (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    ticker        TEXT NOT NULL,
    buy_date      TEXT NOT NULL,
    trailing_high INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, ticker)
);

CREATE TABLE IF NOT EXISTS schedule_config (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL UNIQUE REFERENCES users(id),
    enabled    INTEGER NOT NULL DEFAULT 0,
    weekdays   TEXT NOT NULL DEFAULT '1-5',
    timezone   TEXT NOT NULL DEFAULT 'Asia/Seoul',
    buy_times  TEXT NOT NULL DEFAULT '[]',
    sell_times TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS telegram_subscribers (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    chat_id TEXT NOT NULL,
    UNIQUE(user_id, chat_id)
);

CREATE TABLE IF NOT EXISTS user_prompts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL UNIQUE REFERENCES users(id),
    buy_template    TEXT NOT NULL DEFAULT '',
    sell_template   TEXT NOT NULL DEFAULT '',
    budget_template TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS token_cache (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    broker_type  TEXT NOT NULL,
    access_token TEXT NOT NULL,
    expires_at   INTEGER NOT NULL,
    UNIQUE(user_id, broker_type)
);

CREATE TABLE IF NOT EXISTS telegram_otp (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code       TEXT NOT NULL UNIQUE,
    expires_at INTEGER NOT NULL
);
"""


# ------------------------------------------------------------------
# 연결 관리
# ------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    """컨텍스트 매니저: 커넥션 열고 자동 커밋/롤백."""
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """스키마 생성 + 최초 실행 시 .env 데이터 자동 마이그레이션."""
    with get_db() as conn:
        conn.executescript(_SCHEMA_SQL)

    # 사용자가 0명이면 자동 마이그레이션
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        _migrate_from_legacy()


def is_db_available() -> bool:
    """DB 파일이 존재하고 users 테이블이 있으면 True."""
    if not DB_PATH.exists():
        return False
    try:
        with get_db() as conn:
            conn.execute("SELECT 1 FROM users LIMIT 1")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------
# 마이그레이션 (JSON/.env → DB)
# ------------------------------------------------------------------

def _migrate_from_legacy() -> None:
    """기존 .env + JSON 데이터를 DB로 이관합니다."""
    import config as cfg_module
    from auth import hash_password

    logger.info("[DB 마이그레이션] 기존 데이터를 DB로 이관합니다...")

    now_str = cfg_module.now().strftime("%Y-%m-%d %H:%M:%S")
    admin_pw = os.environ.get("WEB_ADMIN_PASSWORD", "admin")

    with get_db() as conn:
        # 1. admin 사용자 생성
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            ("admin", hash_password(admin_pw), "관리자", now_str, now_str),
        )
        admin_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        logger.info(f"  admin 사용자 생성 (id={admin_id})")

        # 2. .env → user_config
        migrated_keys = 0
        for key in MANAGED_USER_KEYS:
            val = os.environ.get(key, "")
            if val:
                conn.execute(
                    "INSERT OR REPLACE INTO user_config (user_id, key, value) VALUES (?, ?, ?)",
                    (admin_id, key, val),
                )
                migrated_keys += 1
        logger.info(f"  user_config: {migrated_keys}개 키 이관")

        # 3. ai_traces.jsonl → ai_traces
        traces_file = Path("data/ai_traces.jsonl")
        if traces_file.exists():
            count = 0
            for line in traces_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    conn.execute(
                        "INSERT INTO ai_traces (user_id, run_id, mode, trading_mode, event_type, payload, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            admin_id,
                            row.get("run_id", ""),
                            row.get("mode", ""),
                            row.get("trading_mode", ""),
                            row.get("event_type", ""),
                            json.dumps(row.get("payload", {}), ensure_ascii=False),
                            row.get("time", now_str),
                        ),
                    )
                    count += 1
                except Exception:
                    continue
            logger.info(f"  ai_traces: {count}건 이관")

        # 4. web_actions.json → action_history
        actions_file = Path("data/web_actions.json")
        if actions_file.exists():
            try:
                actions = json.loads(actions_file.read_text(encoding="utf-8"))
                for a in actions:
                    conn.execute(
                        "INSERT INTO action_history (user_id, action, status, detail, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (admin_id, a.get("action", ""), a.get("status", ""),
                         a.get("detail", ""), a.get("time", now_str)),
                    )
                logger.info(f"  action_history: {len(actions)}건 이관")
            except Exception as e:
                logger.warning(f"  web_actions.json 이관 실패: {e}")

        # 5. holdings_tracker.json → holdings_tracker
        tracker_file = Path("data/holdings_tracker.json")
        if tracker_file.exists():
            try:
                data = json.loads(tracker_file.read_text(encoding="utf-8"))
                for ticker, info in data.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO holdings_tracker (user_id, ticker, buy_date, trailing_high) "
                        "VALUES (?, ?, ?, ?)",
                        (admin_id, ticker, info.get("buy_date", now_str[:10]),
                         info.get("trailing_high", 0)),
                    )
                logger.info(f"  holdings_tracker: {len(data)}건 이관")
            except Exception as e:
                logger.warning(f"  holdings_tracker.json 이관 실패: {e}")

        # 6. web_schedule.json → schedule_config
        sched_file = Path("data/web_schedule.json")
        if sched_file.exists():
            try:
                s = json.loads(sched_file.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT OR REPLACE INTO schedule_config "
                    "(user_id, enabled, weekdays, timezone, buy_times, sell_times) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        admin_id,
                        1 if s.get("enabled") else 0,
                        s.get("weekdays", "1-5"),
                        s.get("timezone", "Asia/Seoul"),
                        json.dumps(s.get("buy_times", []), ensure_ascii=False),
                        json.dumps(s.get("sell_times", []), ensure_ascii=False),
                    ),
                )
                logger.info("  schedule_config 이관 완료")
            except Exception as e:
                logger.warning(f"  web_schedule.json 이관 실패: {e}")

        # 7. telegram_subscribers.json → telegram_subscribers
        tg_file = Path("data/telegram_subscribers.json")
        if tg_file.exists():
            try:
                subs = json.loads(tg_file.read_text(encoding="utf-8"))
                for chat_id in subs:
                    conn.execute(
                        "INSERT OR REPLACE INTO telegram_subscribers (user_id, chat_id) VALUES (?, ?)",
                        (admin_id, str(chat_id)),
                    )
                logger.info(f"  telegram_subscribers: {len(subs)}건 이관")
            except Exception as e:
                logger.warning(f"  telegram_subscribers.json 이관 실패: {e}")

        # 8. prompts.json → user_prompts
        prompts_file = Path("data/prompts.json")
        if prompts_file.exists():
            try:
                p = json.loads(prompts_file.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT OR REPLACE INTO user_prompts "
                    "(user_id, buy_template, sell_template, budget_template) VALUES (?, ?, ?, ?)",
                    (admin_id, p.get("buy", ""), p.get("sell", ""), p.get("budget", "")),
                )
                logger.info("  user_prompts 이관 완료")
            except Exception as e:
                logger.warning(f"  prompts.json 이관 실패: {e}")

    logger.info("[DB 마이그레이션] 완료")


# ------------------------------------------------------------------
# 사용자 CRUD
# ------------------------------------------------------------------

def get_user_by_id(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_all_users() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT id, username, display_name, is_admin, is_active, created_at FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def create_user(username: str, password_hash: str, display_name: str = "", is_admin: bool = False) -> int:
    import config as cfg_module
    now_str = cfg_module.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, password_hash, display_name, 1 if is_admin else 0, now_str, now_str),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_user(user_id: int, **fields) -> None:
    import config as cfg_module
    allowed = {"display_name", "password_hash", "is_admin", "is_active"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = cfg_module.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", (*updates.values(), user_id))


def delete_user(user_id: int) -> None:
    with get_db() as conn:
        # CASCADE가 작동하지 않을 수 있으므로 관련 데이터를 먼저 삭제
        for table in (
            "user_config", "ai_traces", "action_history", "holdings_tracker",
            "schedule_config", "telegram_subscribers", "user_prompts", "token_cache",
        ):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ------------------------------------------------------------------
# 사용자 설정
# ------------------------------------------------------------------

def get_user_config(user_id: int) -> dict[str, str]:
    """사용자가 DB에 저장한 설정만 반환합니다. 없는 키는 빈 문자열."""
    merged = {key: "" for key in MANAGED_USER_KEYS}

    with get_db() as conn:
        rows = conn.execute(
            "SELECT key, value FROM user_config WHERE user_id = ?", (user_id,)
        ).fetchall()
        for row in rows:
            merged[row["key"]] = row["value"]

    return merged


def set_user_config(user_id: int, key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_config (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, value),
        )


def set_user_config_bulk(user_id: int, config_dict: dict[str, str]) -> None:
    with get_db() as conn:
        for key, value in config_dict.items():
            conn.execute(
                "INSERT OR REPLACE INTO user_config (user_id, key, value) VALUES (?, ?, ?)",
                (user_id, key, value),
            )


# ------------------------------------------------------------------
# AI Traces
# ------------------------------------------------------------------

def insert_trace(user_id: int, run_id: str, mode: str, trading_mode: str,
                 event_type: str, payload: dict, created_at: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO ai_traces (user_id, run_id, mode, trading_mode, event_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, run_id, mode, trading_mode, event_type,
             json.dumps(payload, ensure_ascii=False), created_at),
        )


def get_traces(user_id: int, limit: int = 200, run_id: str = "") -> list[dict]:
    with get_db() as conn:
        if run_id:
            rows = conn.execute(
                "SELECT * FROM ai_traces WHERE user_id = ? AND run_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, run_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_traces WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # 템플릿 호환: created_at → time
            d["time"] = d.get("created_at", "")
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            result.append(d)
        return result


# ------------------------------------------------------------------
# Action History
# ------------------------------------------------------------------

def append_action(user_id: int, action: str, status: str, detail: str, created_at: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO action_history (user_id, action, status, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, action, status, detail, created_at),
        )


def get_actions(user_id: int, limit: int = 200) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM action_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Holdings Tracker
# ------------------------------------------------------------------

def get_holdings_tracker_data(user_id: int) -> dict[str, dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ticker, buy_date, trailing_high FROM holdings_tracker WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {r["ticker"]: {"buy_date": r["buy_date"], "trailing_high": r["trailing_high"]} for r in rows}


def upsert_holding(user_id: int, ticker: str, buy_date: str, trailing_high: int = 0) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO holdings_tracker (user_id, ticker, buy_date, trailing_high) VALUES (?, ?, ?, ?)",
            (user_id, ticker, buy_date, trailing_high),
        )


def update_trailing_high(user_id: int, ticker: str, trailing_high: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE holdings_tracker SET trailing_high = ? WHERE user_id = ? AND ticker = ?",
            (trailing_high, user_id, ticker),
        )


def delete_holding(user_id: int, ticker: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM holdings_tracker WHERE user_id = ? AND ticker = ?", (user_id, ticker))


# ------------------------------------------------------------------
# Schedule Config
# ------------------------------------------------------------------

def get_schedule_config(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM schedule_config WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["buy_times"] = json.loads(d["buy_times"])
        except Exception:
            d["buy_times"] = []
        try:
            d["sell_times"] = json.loads(d["sell_times"])
        except Exception:
            d["sell_times"] = []
        return d


def save_schedule_config(user_id: int, enabled: bool, weekdays: str, timezone: str,
                         buy_times: list[str], sell_times: list[str]) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schedule_config "
            "(user_id, enabled, weekdays, timezone, buy_times, sell_times) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, 1 if enabled else 0, weekdays, timezone,
             json.dumps(buy_times, ensure_ascii=False), json.dumps(sell_times, ensure_ascii=False)),
        )


def get_all_active_schedules() -> list[dict]:
    """활성화된 모든 사용자의 스케줄을 반환합니다."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT s.*, u.username FROM schedule_config s "
            "JOIN users u ON s.user_id = u.id "
            "WHERE s.enabled = 1 AND u.is_active = 1"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["buy_times"] = json.loads(d["buy_times"])
            except Exception:
                d["buy_times"] = []
            try:
                d["sell_times"] = json.loads(d["sell_times"])
            except Exception:
                d["sell_times"] = []
            result.append(d)
        return result


# ------------------------------------------------------------------
# Telegram Subscribers
# ------------------------------------------------------------------

def get_telegram_subscribers(user_id: int) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM telegram_subscribers WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [r["chat_id"] for r in rows]


def add_telegram_subscriber(user_id: int, chat_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO telegram_subscribers (user_id, chat_id) VALUES (?, ?)",
            (user_id, chat_id),
        )


def remove_telegram_subscriber(user_id: int, chat_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM telegram_subscribers WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )


# ------------------------------------------------------------------
# User Prompts
# ------------------------------------------------------------------

def get_user_prompts(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM user_prompts WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def save_user_prompts(user_id: int, buy_template: str, sell_template: str, budget_template: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_prompts (user_id, buy_template, sell_template, budget_template) "
            "VALUES (?, ?, ?, ?)",
            (user_id, buy_template, sell_template, budget_template),
        )


# ------------------------------------------------------------------
# Token Cache
# ------------------------------------------------------------------

def get_cached_token(user_id: int, broker_type: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT access_token, expires_at FROM token_cache WHERE user_id = ? AND broker_type = ?",
            (user_id, broker_type),
        ).fetchone()
        if row and row["expires_at"] > int(time.time()) + 30:
            return {"access_token": row["access_token"], "expires_at": row["expires_at"]}
        return None


def save_cached_token(user_id: int, broker_type: str, access_token: str, expires_at: int) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO token_cache (user_id, broker_type, access_token, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, broker_type, access_token, expires_at),
        )


# ------------------------------------------------------------------
# Telegram OTP
# ------------------------------------------------------------------

def create_telegram_otp(user_id: int, ttl_seconds: int = 300) -> str:
    """사용자를 위한 6자리 OTP를 생성합니다. 기본 5분 유효."""
    import secrets
    code = secrets.token_hex(3).upper()  # 6자리 hex
    expires_at = int(time.time()) + ttl_seconds
    with get_db() as conn:
        # 기존 OTP 삭제
        conn.execute("DELETE FROM telegram_otp WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO telegram_otp (user_id, code, expires_at) VALUES (?, ?, ?)",
            (user_id, code, expires_at),
        )
    return code


def verify_telegram_otp(code: str) -> int | None:
    """OTP 코드를 검증하고 성공 시 user_id를 반환합니다. 실패/만료 시 None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM telegram_otp WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < int(time.time()):
            conn.execute("DELETE FROM telegram_otp WHERE code = ?", (code,))
            return None
        # 사용 후 삭제 (일회용)
        conn.execute("DELETE FROM telegram_otp WHERE code = ?", (code,))
        return row["user_id"]
