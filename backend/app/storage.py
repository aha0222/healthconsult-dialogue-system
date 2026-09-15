"""会话持久化：用标准库 sqlite3 存储会话、消息与审计日志。

表结构（按 schema_version 迁移）：
    v1: sessions(id, personality, created_at, updated_at)
        messages(id, session_id, role, content, risk, created_at)
    v2: audit_log(id, session_id, personality, user_message, reply, risk,
                  violations, fallback_used, model, latency_ms, created_at)
    v3: sessions 增加 summary / profile / summary_upto（滚动摘要与长期画像）
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    personality TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    risk TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    personality TEXT,
    user_message TEXT NOT NULL,
    reply TEXT NOT NULL,
    risk TEXT,
    violations TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id, id);
"""

MEMORY_SCHEMA = """
ALTER TABLE sessions ADD COLUMN summary TEXT;
ALTER TABLE sessions ADD COLUMN profile TEXT;
ALTER TABLE sessions ADD COLUMN summary_upto INTEGER DEFAULT 0;
"""

SCENES_SCHEMA = """
ALTER TABLE messages ADD COLUMN scenes TEXT;
ALTER TABLE audit_log ADD COLUMN scenes TEXT;
"""

MIGRATIONS = [
    (1, SESSIONS_SCHEMA),
    (2, AUDIT_SCHEMA),
    (3, MEMORY_SCHEMA),
    (4, SCENES_SCHEMA),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._migrate(conn)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _migrate(self, conn):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        for version, script in MIGRATIONS:
            if version > current:
                conn.executescript(script)
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )

    def get_schema_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM schema_version"
            ).fetchone()
        return row["v"] or 0

    # ── 会话 ──

    def create_session(self, personality: str) -> str:
        session_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, personality, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, personality, now, now),
            )
        return session_id

    def get_session(self, session_id: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 50):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.*, COUNT(m.id) AS message_count "
                "FROM sessions s LEFT JOIN messages m ON m.session_id = s.id "
                "GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            deleted = cursor.rowcount > 0
        return deleted

    # ── 消息 ──

    def add_message(self, session_id: str, role: str, content: str, risk=None, scenes=None):
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, risk, scenes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    risk,
                    json.dumps(scenes or [], ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )

    def get_messages(self, session_id: str, limit=None):
        """按时间正序返回消息；limit 表示取最近 N 条。"""
        with self._connect() as conn:
            if limit:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            try:
                record["scenes"] = json.loads(record.get("scenes") or "[]")
            except (TypeError, ValueError):
                record["scenes"] = []
            records.append(record)
        return records

    # ── 长期记忆 ──

    def update_memory(self, session_id, summary=None, profile=None, summary_upto=None):
        """更新会话的滚动摘要 / 画像 / 已摘要到的消息 id。"""
        fields = []
        values = []
        if summary is not None:
            fields.append("summary = ?")
            values.append(summary)
        if profile is not None:
            fields.append("profile = ?")
            values.append(profile)
        if summary_upto is not None:
            fields.append("summary_upto = ?")
            values.append(summary_upto)
        if not fields:
            return
        values.append(session_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?", values
            )

    # ── 审计 ──

    def add_audit(
        self,
        session_id,
        personality,
        user_message,
        reply,
        risk=None,
        scenes=None,
        violations=None,
        fallback_used=False,
        model=None,
        latency_ms=None,
    ):
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (session_id, personality, user_message, reply, "
                "risk, scenes, violations, fallback_used, model, latency_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    personality,
                    user_message,
                    reply,
                    risk,
                    json.dumps(scenes or [], ensure_ascii=False),
                    json.dumps(violations or [], ensure_ascii=False),
                    1 if fallback_used else 0,
                    model,
                    latency_ms,
                    now,
                ),
            )

    def list_audit(
        self,
        limit: int = 100,
        session_id=None,
        since=None,
        only_flagged: bool = False,
        risks=None,
    ):
        """查询审计记录。

        since: ISO 时间字符串，只取该时间之后。
        only_flagged: 只看命中兜底（fallback_used）的记录。
        risks: 只取这些风险等级；与 only_flagged 之间是"或"关系。
        """
        clauses = []
        params = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)

        flag_parts = []
        if only_flagged:
            flag_parts.append("fallback_used = 1")
        if risks:
            placeholders = ", ".join("?" for _ in risks)
            flag_parts.append(f"risk IN ({placeholders})")
            params.extend(risks)
        if flag_parts:
            clauses.append("(" + " OR ".join(flag_parts) + ")")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            try:
                record["violations"] = json.loads(record.get("violations") or "[]")
            except (TypeError, ValueError):
                record["violations"] = []
            try:
                record["scenes"] = json.loads(record.get("scenes") or "[]")
            except (TypeError, ValueError):
                record["scenes"] = []
            record["fallback_used"] = bool(record.get("fallback_used"))
            records.append(record)
        return records
