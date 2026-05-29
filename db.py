#!/usr/bin/env python3
"""
PDF Expert 批量 OCR — 统一数据库层

单数据库、多表设计，替代原有的"每批次一个 .db 文件"模式。
核心原则：
1. 文件全局去重（files 表）——同一文件不会被重复 OCR
2. 批次独立追踪（batches 表）——每次运行都有上下文
3. 扫描/OCR/验证分表存储——粒度清晰，可聚合查询
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


# ── 数据库路径 ────────────────────────────────────────────
DEFAULT_DB_PATH = "ocr_history.db"


# ── Schema ────────────────────────────────────────────────
_SCHEMA = """
-- 文件表：全局去重，同一文件只存一条
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT UNIQUE NOT NULL,
    filename    TEXT NOT NULL,
    file_size   INTEGER,
    page_count  INTEGER,
    md5         TEXT,
    first_seen  TEXT NOT NULL
);

-- 批次表：每次 scan + batch_ocr 作为一个批次
CREATE TABLE IF NOT EXISTS batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    source_dir    TEXT NOT NULL,
    output_dir    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    completed_at  TEXT,
    status        TEXT DEFAULT 'running',
    total_files   INTEGER DEFAULT 0,
    config_json   TEXT
);

-- 扫描结果表：每次扫描的结果（文件可多次扫描）
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER NOT NULL REFERENCES files(id),
    batch_id    INTEGER NOT NULL REFERENCES batches(id),
    status      TEXT NOT NULL,
    char_count  INTEGER,
    reason      TEXT,
    scanned_at  TEXT NOT NULL,
    UNIQUE(file_id, batch_id)
);

-- OCR 任务表：实际执行记录
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    batch_id      INTEGER NOT NULL REFERENCES batches(id),
    output_path   TEXT NOT NULL,
    status        TEXT DEFAULT 'pending',
    started_at    TEXT,
    finished_at   TEXT,
    error_msg     TEXT,
    retry_count   INTEGER DEFAULT 0,
    UNIQUE(file_id, batch_id)
);

-- 验证记录表
CREATE TABLE IF NOT EXISTS validations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL REFERENCES tasks(id),
    char_count    INTEGER,
    status        TEXT,
    reason        TEXT,
    validated_at  TEXT NOT NULL
);

-- 常用查询索引
CREATE INDEX IF NOT EXISTS idx_tasks_batch_status ON tasks(batch_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_file        ON tasks(file_id);
CREATE INDEX IF NOT EXISTS idx_scans_batch       ON scans(batch_id);
CREATE INDEX IF NOT EXISTS idx_validations_task  ON validations(task_id);
"""


# ── 连接管理 ──────────────────────────────────────────────
def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """获取数据库连接，自动初始化 Schema。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ── files 表 ──────────────────────────────────────────────
def ensure_file(conn: sqlite3.Connection, path: str, file_size: int | None = None,
                page_count: int | None = None, md5: str | None = None) -> int:
    """确保文件记录存在，返回 file_id。"""
    p = Path(path)
    cursor = conn.execute(
        "SELECT id FROM files WHERE path = ?", (path,)
    )
    row = cursor.fetchone()
    if row:
        return row["id"]

    cursor = conn.execute(
        """
        INSERT INTO files (path, filename, file_size, page_count, md5, first_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (path, p.name, file_size, page_count, md5, _now()),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def get_file_by_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """按路径查询文件记录。"""
    cursor = conn.execute("SELECT * FROM files WHERE path = ?", (path,))
    return cursor.fetchone()


# ── batches 表 ────────────────────────────────────────────
def create_batch(conn: sqlite3.Connection, name: str, source_dir: str,
                 output_dir: str, config: dict | None = None) -> int:
    """创建新批次，返回 batch_id。"""
    cursor = conn.execute(
        """
        INSERT INTO batches (name, source_dir, output_dir, created_at, config_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, source_dir, output_dir, _now(), json.dumps(config) if config else None),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> sqlite3.Row | None:
    """按 ID 查询批次。"""
    cursor = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,))
    return cursor.fetchone()


def list_batches(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """列出最近的批次。"""
    cursor = conn.execute(
        "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return cursor.fetchall()


def update_batch_status(conn: sqlite3.Connection, batch_id: int,
                        status: str, total_files: int | None = None) -> None:
    """更新批次状态。"""
    if status == "completed":
        conn.execute(
            "UPDATE batches SET status = ?, completed_at = ?, total_files = COALESCE(?, total_files) WHERE id = ?",
            (status, _now(), total_files, batch_id),
        )
    else:
        conn.execute(
            "UPDATE batches SET status = ?, total_files = COALESCE(?, total_files) WHERE id = ?",
            (status, total_files, batch_id),
        )
    conn.commit()


# ── scans 表 ──────────────────────────────────────────────
def record_scan(conn: sqlite3.Connection, file_id: int, batch_id: int,
                status: str, char_count: int | None = None, reason: str = "") -> None:
    """记录扫描结果（upsert）。"""
    conn.execute(
        """
        INSERT INTO scans (file_id, batch_id, status, char_count, reason, scanned_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id, batch_id) DO UPDATE SET
            status = excluded.status,
            char_count = excluded.char_count,
            reason = excluded.reason,
            scanned_at = excluded.scanned_at
        """,
        (file_id, batch_id, status, char_count, reason, _now()),
    )
    conn.commit()


def get_scans_by_batch(conn: sqlite3.Connection, batch_id: int,
                       status: str | None = None) -> list[sqlite3.Row]:
    """获取某批次的扫描结果。"""
    if status:
        cursor = conn.execute(
            "SELECT * FROM scans WHERE batch_id = ? AND status = ?",
            (batch_id, status),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM scans WHERE batch_id = ?", (batch_id,)
        )
    return cursor.fetchall()


# ── tasks 表 ──────────────────────────────────────────────
def create_task(conn: sqlite3.Connection, file_id: int, batch_id: int,
                output_path: str) -> int:
    """创建 OCR 任务，返回 task_id。"""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO tasks (file_id, batch_id, output_path, status)
        VALUES (?, ?, ?, 'pending')
        """,
        (file_id, batch_id, output_path),
    )
    conn.commit()
    return cursor.lastrowid or _get_task_id(conn, file_id, batch_id)  # type: ignore[return-value]


def _get_task_id(conn: sqlite3.Connection, file_id: int, batch_id: int) -> int:
    """获取已存在的 task_id。"""
    cursor = conn.execute(
        "SELECT id FROM tasks WHERE file_id = ? AND batch_id = ?",
        (file_id, batch_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"Task not found: file_id={file_id}, batch_id={batch_id}")
    return row["id"]


def get_task_stats(conn: sqlite3.Connection, batch_id: int | None = None) -> dict[str, int]:
    """获取任务统计。"""
    if batch_id is not None:
        cursor = conn.execute(
            "SELECT status, COUNT(*) FROM tasks WHERE batch_id = ? GROUP BY status",
            (batch_id,),
        )
    else:
        cursor = conn.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        )
    stats = {row[0]: row[1] for row in cursor.fetchall()}
    total = sum(stats.values())
    return {
        "total": total,
        "pending": stats.get("pending", 0),
        "processing": stats.get("processing", 0),
        "success": stats.get("success", 0),
        "failed": stats.get("failed", 0),
        "timeout": stats.get("timeout", 0),
    }


def get_next_pending_task(conn: sqlite3.Connection, batch_id: int) -> sqlite3.Row | None:
    """获取下一个待处理任务。"""
    cursor = conn.execute(
        """
        SELECT t.id, t.file_id, t.output_path, f.path AS input_path
        FROM tasks t
        JOIN files f ON t.file_id = f.id
        WHERE t.batch_id = ? AND t.status = 'pending'
        ORDER BY t.id
        LIMIT 1
        """,
        (batch_id,),
    )
    return cursor.fetchone()


def mark_task_processing(conn: sqlite3.Connection, task_id: int) -> None:
    """标记任务为处理中。"""
    conn.execute(
        "UPDATE tasks SET status = 'processing', started_at = ? WHERE id = ?",
        (_now(), task_id),
    )
    conn.commit()


def mark_task_done(conn: sqlite3.Connection, task_id: int, status: str,
                   error_msg: str = "") -> None:
    """标记任务完成。"""
    conn.execute(
        """
        UPDATE tasks
        SET status = ?, finished_at = ?, error_msg = ?
        WHERE id = ?
        """,
        (status, _now(), error_msg, task_id),
    )
    conn.commit()


def reset_processing_tasks(conn: sqlite3.Connection, batch_id: int | None = None) -> int:
    """将 processing 状态重置为 pending，返回重置数量。"""
    if batch_id is not None:
        cursor = conn.execute(
            "UPDATE tasks SET status = 'pending' WHERE batch_id = ? AND status = 'processing'",
            (batch_id,),
        )
    else:
        cursor = conn.execute(
            "UPDATE tasks SET status = 'pending' WHERE status = 'processing'"
        )
    conn.commit()
    return cursor.rowcount


def get_success_tasks(conn: sqlite3.Connection, batch_id: int | None = None) -> list[dict[str, Any]]:
    """获取所有成功任务（用于验证阶段）。"""
    if batch_id is not None:
        cursor = conn.execute(
            """
            SELECT f.path AS input_path, t.output_path
            FROM tasks t
            JOIN files f ON t.file_id = f.id
            WHERE t.batch_id = ? AND t.status = 'success'
            ORDER BY t.id
            """,
            (batch_id,),
        )
    else:
        cursor = conn.execute(
            """
            SELECT f.path AS input_path, t.output_path
            FROM tasks t
            JOIN files f ON t.file_id = f.id
            WHERE t.status = 'success'
            ORDER BY t.id
            """
        )
    return [{"input": row["input_path"], "output": row["output_path"]} for row in cursor.fetchall()]


def get_all_tasks_with_details(conn: sqlite3.Connection,
                               batch_id: int | None = None) -> list[dict[str, Any]]:
    """获取所有任务详情（用于报告生成）。"""
    if batch_id is not None:
        cursor = conn.execute(
            """
            SELECT f.path AS input_path, t.output_path, t.status, t.error_msg,
                   t.started_at, t.finished_at
            FROM tasks t
            JOIN files f ON t.file_id = f.id
            WHERE t.batch_id = ?
            ORDER BY t.id
            """,
            (batch_id,),
        )
    else:
        cursor = conn.execute(
            """
            SELECT f.path AS input_path, t.output_path, t.status, t.error_msg,
                   t.started_at, t.finished_at
            FROM tasks t
            JOIN files f ON t.file_id = f.id
            ORDER BY t.id
            """
        )
    return [
        {
            "input": row["input_path"],
            "output": row["output_path"],
            "status": row["status"],
            "error": row["error_msg"] or "",
            "started": row["started_at"] or "",
            "finished": row["finished_at"] or "",
        }
        for row in cursor.fetchall()
    ]


# ── 去重逻辑：检查文件是否已有成功记录 ─────────────────────
def has_successful_ocr(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    """检查某文件是否已有成功的 OCR 记录（任意批次）。

    返回 {"task_id": int, "output_path": str} 或 None。
    """
    cursor = conn.execute(
        """
        SELECT t.id AS task_id, t.output_path
        FROM tasks t
        JOIN files f ON t.file_id = f.id
        WHERE f.path = ? AND t.status = 'success'
        ORDER BY t.finished_at DESC
        LIMIT 1
        """,
        (path,),
    )
    row = cursor.fetchone()
    if row:
        return {"task_id": row["task_id"], "output_path": row["output_path"]}
    return None


# ── validations 表 ────────────────────────────────────────
def record_validation(conn: sqlite3.Connection, task_id: int, char_count: int | None,
                        status: str, reason: str = "") -> None:
    """记录验证结果。"""
    conn.execute(
        """
        INSERT INTO validations (task_id, char_count, status, reason, validated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (task_id, char_count, status, reason, _now()),
    )
    conn.commit()


def get_validation_summary(conn: sqlite3.Connection,
                           batch_id: int | None = None) -> dict[str, Any]:
    """获取验证摘要统计。"""
    if batch_id is not None:
        cursor = conn.execute(
            """
            SELECT v.status, COUNT(*)
            FROM validations v
            JOIN tasks t ON v.task_id = t.id
            WHERE t.batch_id = ?
            GROUP BY v.status
            """,
            (batch_id,),
        )
    else:
        cursor = conn.execute(
            """
            SELECT status, COUNT(*) FROM validations GROUP BY status
            """
        )
    stats = {row[0]: row[1] for row in cursor.fetchall()}
    total = sum(stats.values())
    return {
        "total": total,
        "pass": stats.get("pass", 0),
        "fail": stats.get("fail", 0),
        "error": stats.get("error", 0),
        "pass_rate": round(stats.get("pass", 0) / total * 100, 2) if total else 0.0,
    }


# ── 工具函数 ──────────────────────────────────────────────
def _now() -> str:
    return datetime.now().isoformat()
