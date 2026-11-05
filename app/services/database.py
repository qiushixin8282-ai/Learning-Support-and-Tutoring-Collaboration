"""SQLite 数据库初始化与连接工具。

一期使用 SQLite 以降低本地运行门槛；SQL 均集中在此处，后续可迁移为
SQLAlchemy + PostgreSQL，而不改变 API 与权限服务的职责边界。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def database_path() -> Path:
    """从环境变量读取 SQLite 文件位置，默认写入本地 instance 目录。"""

    configured = os.getenv("DATABASE_PATH", "instance/lab_support.sqlite3")
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """提供启用外键、按列读取的数据库连接。"""

    conn = sqlite3.connect(database_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database() -> None:
    """创建一期登录、提示词和审计所需的最小表结构。"""

    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                version INTEGER NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_by_user_id INTEGER,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(updated_by_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                user_id INTEGER,
                role TEXT,
                event_type TEXT NOT NULL,
                result_status TEXT NOT NULL,
                detail TEXT NOT NULL,
                risk_flag TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS knowledge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'Markdown',
                visibility TEXT NOT NULL DEFAULT '全员可见',
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '已发布',
                created_by_user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(created_by_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS support_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                owner TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT '普通',
                status TEXT NOT NULL DEFAULT '待分配',
                due_date TEXT,
                created_by_user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(created_by_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_item_id INTEGER NOT NULL,
                chunk_no INTEGER NOT NULL,
                content TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(knowledge_item_id, chunk_no),
                FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS rag_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                source_ids TEXT NOT NULL,
                model_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_item ON knowledge_chunks(knowledge_item_id);
            CREATE INDEX IF NOT EXISTS idx_rag_queries_created ON rag_queries(created_at);
            """
        )
        knowledge_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_items)").fetchall()}
        migrations = {
            "original_name": "TEXT",
            "stored_name": "TEXT",
            "mime_type": "TEXT",
            "size_bytes": "INTEGER NOT NULL DEFAULT 0",
            "page_count": "INTEGER NOT NULL DEFAULT 0",
            "char_count": "INTEGER NOT NULL DEFAULT 0",
            "parse_status": "TEXT NOT NULL DEFAULT '已解析'",
        }
        for column, definition in migrations.items():
            if column not in knowledge_columns:
                conn.execute(f"ALTER TABLE knowledge_items ADD COLUMN {column} {definition}")
        default_prompt = (
            "你是学习支持协作实验室的资料助手，只根据提供的资料内容回答，"
            "不确定时明确说'资料中未找到'，回答末尾附引用来源（文件名+切片序号），不得编造。"
        )
        exists = conn.execute("SELECT 1 FROM prompt_templates WHERE is_active = 1").fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO prompt_templates(name, content, version, is_active) VALUES (?, ?, 1, 1)",
                ("默认资料问答提示词", default_prompt),
            )
