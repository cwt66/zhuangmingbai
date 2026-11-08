# -*- coding: utf-8 -*-
"""审计结果持久化：PostgreSQL（云上）/ SQLite（本地与离线演示）。

为什么要有这一层
---------------
赛题要求关联 RDS：不是"开了一台数据库"，而是"业务数据真的落进去了"。
本模块把每次审计的**可复核摘要**（不是 HTML）写进库，支持 /history 回看、
/api/audits 拉取、以及后续「同一个项目多次报价」的纵向对比。

设计边界
--------
- 引擎仍然零依赖：本模块只用标准库 sqlite3；PostgreSQL 走可选依赖 psycopg。
- 云上失败必须**降级而不是崩**：连不上 PG 时自动退回 SQLite，并在 /health 里
  明确写 `degraded` 原因。演示可以断网，但不能因为数据库挂了整个作品打不开。
- 只存摘要与结构化 payload，不存原始单据文件（原件在 OBS，本地不留）。
"""
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SQLITE = os.path.join(BASE, "data", "state.db")

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    template_key TEXT,
    scope TEXT,
    doc_count INTEGER,
    docs TEXT,
    total REAL,
    missing_cost REAL,
    duplicate_cut REAL,
    effective REAL,
    fingerprint TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    audit_id TEXT,
    role TEXT,
    useful INTEGER,
    note TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_created ON audits(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_audit ON feedback(audit_id);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    template_key TEXT,
    scope TEXT,
    doc_count INTEGER,
    docs TEXT,
    total DOUBLE PRECISION,
    missing_cost DOUBLE PRECISION,
    duplicate_cut DOUBLE PRECISION,
    effective DOUBLE PRECISION,
    fingerprint TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    audit_id TEXT,
    role TEXT,
    useful INTEGER,
    note TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_created ON audits(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_audit ON feedback(audit_id);
"""


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix="A"):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


class Store:
    """最小可用仓储。任何异常都不向 Web 层抛，只记录 degraded 原因。"""

    def __init__(self, url=None):
        self.raw = (url if url is not None else os.environ.get("DATABASE_URL", "")).strip()
        self.kind = "sqlite"
        self.path = DEFAULT_SQLITE
        self.dsn = None
        self.degraded = None
        self._lock = threading.Lock()
        self._init_backend()
        self._init_schema()

    # ---------------------------------------------------------------- backend
    def _init_backend(self):
        if self.raw.startswith(("postgres://", "postgresql://")):
            try:
                import psycopg  # noqa: F401
                self.kind = "postgres"
                self.dsn = self.raw
                return
            except Exception as e:  # noqa: BLE001
                self.degraded = "psycopg 未安装，已回退 SQLite：%s" % e
        elif self.raw.startswith("sqlite:///"):
            self.path = self.raw[len("sqlite:///"):] or DEFAULT_SQLITE
        elif self.raw and not self.raw.startswith("sqlite"):
            self.degraded = "无法识别的 DATABASE_URL，已回退 SQLite"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

    def _pg(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def _sqlite(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _sqlite_cm(self):
        """sqlite3 的 `with conn:` 只负责 commit/rollback，**不会关闭连接**。

        之前两处 `with self._sqlite() as c` 会在每次审计/初始化后泄漏一个连接，
        严格模式（-W error::ResourceWarning）会报警。这里包一层 closing，
        同时保留 `with conn` 的自动提交语义。
        """
        conn = self._sqlite()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self):
        try:
            with self._lock:
                if self.kind == "postgres":
                    with self._pg() as c:
                        with c.cursor() as cur:
                            # psycopg 不建议一次 execute 多条语句；逐条执行，失败信息更清楚
                            for stmt in SCHEMA_POSTGRES.split(";"):
                                if stmt.strip():
                                    cur.execute(stmt)
                else:
                    with self._sqlite_cm() as c:
                        c.executescript(SCHEMA_SQLITE)
        except Exception as e:  # noqa: BLE001
            # 文档承诺「连不上 PG 自动降级 SQLite」。原来的实现在这里直接置
            # kind="down"（完全不可用），只写了句降级说明，其实没有降级——
            # 2026-11-08 失效矩阵在 CI 上装了 psycopg 时抓到了这个不符。
            # 现在真的回退：清掉 dsn、按 sqlite 重建 schema，degraded 说清原因。
            self.degraded = "PostgreSQL 初始化失败，已回退 SQLite：%s" % e
            self.kind = "sqlite"
            self.dsn = None
            try:
                with self._sqlite_cm() as c:
                    c.executescript(SCHEMA_SQLITE)
            except Exception as e2:  # noqa: BLE001
                self.degraded = "回退 SQLite 也失败：%s" % e2
                self.kind = "down"

    # ---------------------------------------------------------------- queries
    def _q(self, sql):
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def _execute(self, sql, params=()):
        if self.kind == "down":
            return []
        with self._lock:
            if self.kind == "postgres":
                with self._pg() as c:
                    with c.cursor() as cur:
                        cur.execute(self._q(sql), params)
                        try:
                            return cur.fetchall()
                        except Exception:  # noqa: BLE001
                            return []
            with self._sqlite_cm() as c:
                cur = c.execute(sql, params)
                try:
                    return cur.fetchall()
                except Exception:  # noqa: BLE001
                    return []

    # ---------------------------------------------------------------- public
    def save_audit(self, record):
        """record 由 main._persist 组装；id 缺省自动生成。返回 audit_id。"""
        aid = record.get("id") or new_id()
        docs = json.dumps(record.get("docs") or [], ensure_ascii=False)
        payload = json.dumps(record.get("payload") or {}, ensure_ascii=False, default=str)
        row = (
            aid, record.get("created_at") or _now(), record.get("source") or "upload",
            record.get("template_key"), record.get("scope"), int(record.get("doc_count") or 0),
            docs, float(record.get("total") or 0), float(record.get("missing_cost") or 0),
            float(record.get("duplicate_cut") or 0), float(record.get("effective") or 0),
            record.get("fingerprint"), payload,
        )
        try:
            self._execute(
                "INSERT INTO audits (id, created_at, source, template_key, scope, doc_count,"
                " docs, total, missing_cost, duplicate_cut, effective, fingerprint, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        except Exception as e:  # noqa: BLE001
            self.degraded = "写入审计记录失败：%s" % e
        return aid

    def list_audits(self, limit=50):
        rows = self._execute(
            "SELECT id, created_at, source, template_key, scope, doc_count, docs, total,"
            " missing_cost, duplicate_cut, effective, fingerprint"
            " FROM audits ORDER BY created_at DESC LIMIT ?", (int(limit),))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["docs"] = json.loads(d.get("docs") or "[]")
            except Exception:  # noqa: BLE001
                d["docs"] = []
            out.append(d)
        return out

    def get_audit(self, audit_id):
        rows = self._execute("SELECT * FROM audits WHERE id = ?", (audit_id,))
        if not rows:
            return None
        d = dict(rows[0])
        for k in ("docs", "payload"):
            try:
                d[k] = json.loads(d.get(k) or ("[]" if k == "docs" else "{}"))
            except Exception:  # noqa: BLE001
                pass
        return d

    def save_feedback(self, audit_id, role="owner", useful=True, note=""):
        fid = new_id("F")
        try:
            self._execute(
                "INSERT INTO feedback (id, audit_id, role, useful, note, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (fid, audit_id, role, 1 if useful else 0, (note or "")[:2000], _now()))
        except Exception as e:  # noqa: BLE001
            self.degraded = "写入反馈失败：%s" % e
        return fid

    def feedback_for(self, audit_id):
        rows = self._execute(
            "SELECT id, role, useful, note, created_at FROM feedback"
            " WHERE audit_id = ? ORDER BY created_at DESC", (audit_id,))
        return [dict(r) for r in rows]

    def stats(self):
        try:
            rows = self._execute("SELECT COUNT(*) AS n FROM audits")
            n = int(dict(rows[0]).get("n") or 0) if rows else 0
        except Exception:  # noqa: BLE001
            n = 0
        return {"kind": self.kind, "degraded": self.degraded, "audits": n, "path": self.path}

    def health(self):
        if self.kind == "down":
            return {"status": "down", "kind": "down", "degraded": self.degraded}
        try:
            self._execute("SELECT 1")
            status = "ok"
        except Exception:  # noqa: BLE001
            status = "down"
        return {"status": status, "kind": self.kind, "degraded": self.degraded,
                "path": self.path if self.kind == "sqlite" else None}


_shared = None
_shared_lock = threading.Lock()


def get_store():
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Store()
        return _shared
