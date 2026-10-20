"""Lưu trữ SQLite: readings (số liệu thô, có hash để truy vết) + verifications."""
import hashlib
import json
import os
import sqlite3
import threading
from typing import List, Optional

from .config import DB_PATH
from .models import NewsItem, Reading

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _init(_conn)
    return _conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, ts TEXT, asset_class TEXT, key TEXT, name TEXT,
            source TEXT, source_url TEXT, value REAL, quote TEXT,
            ok INTEGER, error TEXT, raw TEXT, content_hash TEXT
        );
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, ts TEXT, asset_class TEXT, key TEXT, name TEXT, quote TEXT,
            consensus REAL, num_sources INTEGER, max_diff_pct REAL,
            change_pct REAL, status TEXT, notes TEXT, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, fetched_at TEXT, source TEXT, title TEXT, link TEXT,
            published TEXT, summary TEXT, classes TEXT, content_hash TEXT
        );
        CREATE TABLE IF NOT EXISTS news_enrich (
            link TEXT PRIMARY KEY,
            vi_title TEXT, vi_summary TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS notifications (
            key TEXT PRIMARY KEY, sent_at TEXT
        );
        CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY, v TEXT
        );
        -- Nhật ký lệnh của người dùng + kết quả đánh giá rủi ro (lúc vào lệnh và lần gần nhất).
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, source TEXT, key TEXT, symbol TEXT, name TEXT, cls TEXT, side TEXT,
            entry REAL, stop REAL, target REAL, qty REAL, note TEXT,
            status TEXT DEFAULT 'open', close_price REAL, closed_at TEXT,
            risk_entry INTEGER, risk_now INTEGER, level_now TEXT, eval_json TEXT,
            alerts TEXT DEFAULT '[]', alert_score INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_readings_key ON readings(key, ts);
        CREATE INDEX IF NOT EXISTS idx_verif_key ON verifications(key, ts);
        CREATE INDEX IF NOT EXISTS idx_news_run ON news(run_id);
        """
    )
    # Bổ sung cột cho database tạo từ phiên bản cũ.
    for table, col in (("readings", "day_chg"), ("verifications", "day_chg_pct")):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL")
    conn.commit()


def _hash(r: Reading) -> str:
    payload = f"{r.source}|{r.key}|{r.value}|{r.ts}|{r.raw}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def save_readings(run_id: str, readings: List[Reading]) -> None:
    with _lock:
        conn = _connect()
        conn.executemany(
            """INSERT INTO readings
               (run_id, ts, asset_class, key, name, source, source_url, value,
                quote, ok, error, raw, content_hash, day_chg)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (run_id, r.ts, r.asset_class, r.key, r.name, r.source, r.source_url,
                 r.value, r.quote, int(r.ok), r.error, r.raw, _hash(r), r.day_chg)
                for r in readings
            ],
        )
        conn.commit()


def save_verifications(run_id: str, results: List[dict]) -> None:
    with _lock:
        conn = _connect()
        conn.executemany(
            """INSERT INTO verifications
               (run_id, ts, asset_class, key, name, quote, consensus, num_sources,
                max_diff_pct, change_pct, status, notes, detail, day_chg_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (run_id, v["ts"], v["asset_class"], v["key"], v["name"], v["quote"],
                 v["consensus"], v["num_sources"], v["max_diff_pct"], v["change_pct"],
                 v["status"], v["notes"], json.dumps(v["detail"], ensure_ascii=False),
                 v.get("day_chg_pct"))
                for v in results
            ],
        )
        conn.commit()


def save_news(run_id: str, items: List[NewsItem]) -> None:
    with _lock:
        conn = _connect()
        conn.executemany(
            """INSERT INTO news
               (run_id, fetched_at, source, title, link, published, summary, classes, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (run_id, n.fetched_at, n.source, n.title, n.link, n.published,
                 n.summary, ",".join(n.classes),
                 hashlib.sha256(n.link.encode("utf-8")).hexdigest()[:16])
                for n in items
            ],
        )
        conn.commit()


def get_news_items(run_id: str) -> List[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT source, title, link, published, summary, classes FROM news WHERE run_id=?",
            (run_id,),
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["classes"] = d["classes"].split(",") if d["classes"] else []
        out.append(d)
    return out


def get_translations(links: List[str]) -> dict:
    """Trả về {link: {'vi_title':..., 'vi_summary':...}} cho các link đã có bản dịch."""
    if not links:
        return {}
    with _lock:
        conn = _connect()
        qs = ",".join("?" * len(links))
        rows = conn.execute(
            f"SELECT link, vi_title, vi_summary FROM news_enrich WHERE link IN ({qs})",
            links,
        ).fetchall()
    return {r["link"]: {"vi_title": r["vi_title"], "vi_summary": r["vi_summary"]} for r in rows}


def upsert_translations(records: List[dict]) -> None:
    """records: [{'link','vi_title','vi_summary','updated_at'}]"""
    if not records:
        return
    with _lock:
        conn = _connect()
        conn.executemany(
            """INSERT INTO news_enrich (link, vi_title, vi_summary, updated_at)
               VALUES (:link, :vi_title, :vi_summary, :updated_at)
               ON CONFLICT(link) DO UPDATE SET
                 vi_title=excluded.vi_title,
                 vi_summary=excluded.vi_summary,
                 updated_at=excluded.updated_at""",
            records,
        )
        conn.commit()


def filter_unsent(keys: List[str]) -> List[str]:
    """Trả về các khoá thông báo CHƯA từng gửi (giữ thứ tự)."""
    if not keys:
        return []
    with _lock:
        conn = _connect()
        qs = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT key FROM notifications WHERE key IN ({qs})", keys
        ).fetchall()
    sent = {r["key"] for r in rows}
    return [k for k in keys if k not in sent]


def mark_sent(keys: List[str], ts: str) -> None:
    if not keys:
        return
    with _lock:
        conn = _connect()
        conn.executemany(
            "INSERT OR IGNORE INTO notifications (key, sent_at) VALUES (?, ?)",
            [(k, ts) for k in keys],
        )
        conn.commit()


def db_query(sql: str, params: tuple = ()) -> List[dict]:
    with _lock:
        conn = _connect()
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def db_exec(sql: str, params: tuple = ()) -> int:
    """Thực thi câu lệnh ghi; trả về lastrowid."""
    with _lock:
        conn = _connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def kv_get(k: str) -> Optional[str]:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return row["v"] if row else None


def kv_set(k: str, v: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
        conn.commit()


def consensus_history(key: str, n: int = 24) -> List[float]:
    """n giá trị đồng thuận gần nhất (cũ -> mới) để vẽ sparkline."""
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """SELECT consensus FROM verifications
               WHERE key=? AND consensus IS NOT NULL
               ORDER BY ts DESC LIMIT ?""",
            (key, n),
        ).fetchall()
    return [r["consensus"] for r in reversed(rows)]


def consensus_between(key: str, start_ts: str, end_ts: str) -> Optional[float]:
    """Giá đồng thuận mới nhất trong khoảng [start_ts, end_ts]; None nếu không có lần
    thu thập nào trong khoảng đó (vd server đã tắt) — tránh so với dữ liệu quá cũ."""
    with _lock:
        conn = _connect()
        row = conn.execute(
            """SELECT consensus FROM verifications
               WHERE key=? AND ts >= ? AND ts <= ? AND consensus IS NOT NULL
               ORDER BY ts DESC LIMIT 1""",
            (key, start_ts, end_ts),
        ).fetchone()
    return row["consensus"] if row else None


def last_consensus(key: str, before_ts: str) -> Optional[float]:
    """Giá đồng thuận gần nhất trước thời điểm before_ts (để phát hiện bất thường)."""
    with _lock:
        conn = _connect()
        row = conn.execute(
            """SELECT consensus FROM verifications
               WHERE key=? AND ts < ? AND consensus IS NOT NULL
               ORDER BY ts DESC LIMIT 1""",
            (key, before_ts),
        ).fetchone()
    return row["consensus"] if row else None


def latest_run() -> Optional[str]:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT run_id FROM verifications ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return row["run_id"] if row else None


def get_run(run_id: str) -> List[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM verifications WHERE run_id=? ORDER BY asset_class, key",
            (run_id,),
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else []
        out.append(d)
    return out
