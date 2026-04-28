"""
SQLite database layer for sunworks_scheduler.
Handles all CRUD operations for projects, tasks, and daily logs.
"""
import sqlite3
import json
from pathlib import Path
from datetime import date, datetime
from contextlib import contextmanager
from typing import Optional

DB_PATH = Path(__file__).parent / "scheduler.db"


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    conn = sqlite3.connect(str(db_path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    """Create all tables if they don't exist."""
    with get_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active   INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                row_num         INTEGER,
                wbs             TEXT,
                task_name       TEXT NOT NULL,
                duration_days   REAL,
                start_date      DATE,
                finish_date     DATE,
                predecessors    TEXT,       -- raw string from import
                pct_complete    REAL DEFAULT 0,
                wbs_level       INTEGER,    -- derived: depth of WBS (1=phase, 2=section, etc.)
                is_summary      INTEGER DEFAULT 0,  -- 1 if this is a rollup row
                notes           TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS daily_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                log_date    DATE NOT NULL DEFAULT (date('now')),
                pct_before  REAL,
                pct_after   REAL NOT NULL,
                comment     TEXT,
                logged_by   TEXT DEFAULT 'user',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS task_dependencies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                from_row_num    INTEGER,
                to_row_num      INTEGER,
                dep_type        TEXT DEFAULT 'FS',   -- FS, SS, FF, SF
                lag_days        REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_wbs ON tasks(wbs);
            CREATE INDEX IF NOT EXISTS idx_daily_logs_task ON daily_logs(task_id);
            CREATE INDEX IF NOT EXISTS idx_daily_logs_date ON daily_logs(log_date);
        """)


# ─── Projects ────────────────────────────────────────────────────────────────

def create_project(name: str, description: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, description) VALUES (?, ?)",
            (name, description),
        )
        return cur.lastrowid


def list_projects() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_project(project_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_project(project_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# ─── Tasks ───────────────────────────────────────────────────────────────────

def upsert_tasks(project_id: int, tasks: list[dict]) -> None:
    """Bulk-insert tasks, replacing existing ones for the project."""
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        conn.execute(
            "DELETE FROM task_dependencies WHERE project_id = ?", (project_id,)
        )
        conn.executemany(
            """INSERT INTO tasks
               (project_id, row_num, wbs, task_name, duration_days,
                start_date, finish_date, predecessors, pct_complete,
                wbs_level, is_summary, notes)
               VALUES
               (:project_id, :row_num, :wbs, :task_name, :duration_days,
                :start_date, :finish_date, :predecessors, :pct_complete,
                :wbs_level, :is_summary, :notes)""",
            tasks,
        )


def get_tasks(project_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY row_num",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_task_progress(task_id: int, pct_complete: float, comment: str = "", logged_by: str = "user") -> None:
    """Update % complete and write a daily log entry."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pct_complete FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        pct_before = row["pct_complete"] if row else 0.0

        conn.execute(
            "UPDATE tasks SET pct_complete = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pct_complete, task_id),
        )
        conn.execute(
            """INSERT INTO daily_logs (task_id, pct_before, pct_after, comment, logged_by)
               VALUES (?, ?, ?, ?, ?)""",
            (task_id, pct_before, pct_complete, comment, logged_by),
        )


def update_task_dates(task_id: int, start_date: str, finish_date: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET start_date = ?, finish_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (start_date, finish_date, task_id),
        )


# ─── Daily logs ──────────────────────────────────────────────────────────────

def get_daily_logs(project_id: int, log_date: Optional[str] = None) -> list[dict]:
    with get_conn() as conn:
        if log_date:
            rows = conn.execute(
                """SELECT l.*, t.task_name, t.wbs
                   FROM daily_logs l
                   JOIN tasks t ON t.id = l.task_id
                   WHERE t.project_id = ? AND l.log_date = ?
                   ORDER BY l.created_at DESC""",
                (project_id, log_date),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT l.*, t.task_name, t.wbs
                   FROM daily_logs l
                   JOIN tasks t ON t.id = l.task_id
                   WHERE t.project_id = ?
                   ORDER BY l.log_date DESC, l.created_at DESC""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_tasks_active_on(project_id: int, on_date: str) -> list[dict]:
    """Return tasks whose date range includes on_date."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks
               WHERE project_id = ?
                 AND start_date <= ?
                 AND finish_date >= ?
                 AND is_summary = 0
               ORDER BY row_num""",
            (project_id, on_date, on_date),
        ).fetchall()
        return [dict(r) for r in rows]
