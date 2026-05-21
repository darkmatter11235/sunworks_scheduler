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

            CREATE TABLE IF NOT EXISTS sites (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                description     TEXT,
                anchor_start_date DATE,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active       INTEGER DEFAULT 1,
                UNIQUE(project_id, name)
            );

            CREATE TABLE IF NOT EXISTS task_templates (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id          INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                task_name           TEXT NOT NULL,
                default_unit        TEXT,
                default_duration    REAL,
                default_wbs         TEXT,
                wbs_level           INTEGER,
                is_summary          INTEGER DEFAULT 0,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, task_name)
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

            CREATE TABLE IF NOT EXISTS task_formula_dependencies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                site_id         INTEGER REFERENCES sites(id) ON DELETE CASCADE,
                from_row_num    INTEGER,
                to_row_num      INTEGER,
                dep_type        TEXT DEFAULT 'FS',
                lag_days        REAL DEFAULT 0,
                source_formula  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_wbs ON tasks(wbs);
            CREATE INDEX IF NOT EXISTS idx_daily_logs_task ON daily_logs(task_id);
            CREATE INDEX IF NOT EXISTS idx_daily_logs_date ON daily_logs(log_date);
            CREATE INDEX IF NOT EXISTS idx_sites_project ON sites(project_id);
            CREATE INDEX IF NOT EXISTS idx_templates_project ON task_templates(project_id);
            CREATE INDEX IF NOT EXISTS idx_formula_deps_project ON task_formula_dependencies(project_id);
            CREATE INDEX IF NOT EXISTS idx_formula_deps_site ON task_formula_dependencies(site_id);
        """)

        _ensure_task_columns(conn)
        _ensure_site_columns(conn)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(r[1]) for r in rows}


def _ensure_task_columns(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "tasks")
    if "site_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE")
    if "template_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN template_id INTEGER REFERENCES task_templates(id) ON DELETE SET NULL")
    if "quantity" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN quantity REAL")
    if "unit" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN unit TEXT")
    if "planned_start_formula" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN planned_start_formula TEXT")
    if "planned_finish_formula" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN planned_finish_formula TEXT")
    if "planned_start_offset_days" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN planned_start_offset_days REAL")
    if "planned_finish_offset_days" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN planned_finish_offset_days REAL")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_site ON tasks(site_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_template ON tasks(template_id)")


def _ensure_site_columns(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "sites")
    if "anchor_start_date" not in cols:
        conn.execute("ALTER TABLE sites ADD COLUMN anchor_start_date DATE")


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


# ─── Sites ───────────────────────────────────────────────────────────────────

def create_site(project_id: int, name: str, description: str = "") -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM sites WHERE project_id = ? AND lower(name) = lower(?)",
            (project_id, name.strip()),
        ).fetchone()
        if row:
            return int(row["id"])

        cur = conn.execute(
            "INSERT INTO sites (project_id, name, description) VALUES (?, ?, ?)",
            (project_id, name.strip(), description.strip()),
        )
        return int(cur.lastrowid)


def list_sites(project_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sites WHERE project_id = ? ORDER BY created_at ASC, id ASC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_site(site_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        return dict(row) if row else None


def set_site_anchor_start(site_id: int, anchor_start_date: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sites SET anchor_start_date = ? WHERE id = ?",
            (anchor_start_date, site_id),
        )


# ─── Tasks ───────────────────────────────────────────────────────────────────

def upsert_tasks(project_id: int, tasks: list[dict]) -> None:
    """Bulk-insert tasks, replacing existing ones for the project."""
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        conn.execute(
            "DELETE FROM task_dependencies WHERE project_id = ?", (project_id,)
        )
        prepared = []
        for task in tasks:
            prepared.append(
                {
                    "project_id": project_id,
                    "row_num": task.get("row_num"),
                    "wbs": task.get("wbs", ""),
                    "task_name": task.get("task_name", ""),
                    "duration_days": task.get("duration_days"),
                    "start_date": task.get("start_date"),
                    "finish_date": task.get("finish_date"),
                    "predecessors": task.get("predecessors", ""),
                    "pct_complete": task.get("pct_complete", 0.0),
                    "wbs_level": task.get("wbs_level", 1),
                    "is_summary": task.get("is_summary", 0),
                    "notes": task.get("notes", ""),
                    "site_id": task.get("site_id"),
                    "template_id": task.get("template_id"),
                    "quantity": task.get("quantity"),
                    "unit": task.get("unit"),
                    "planned_start_formula": task.get("planned_start_formula"),
                    "planned_finish_formula": task.get("planned_finish_formula"),
                    "planned_start_offset_days": task.get("planned_start_offset_days"),
                    "planned_finish_offset_days": task.get("planned_finish_offset_days"),
                }
            )
        conn.executemany(
            """INSERT INTO tasks
               (project_id, row_num, wbs, task_name, duration_days,
                start_date, finish_date, predecessors, pct_complete,
                wbs_level, is_summary, notes, site_id, template_id, quantity, unit,
                planned_start_formula, planned_finish_formula,
                planned_start_offset_days, planned_finish_offset_days)
               VALUES
               (:project_id, :row_num, :wbs, :task_name, :duration_days,
                :start_date, :finish_date, :predecessors, :pct_complete,
                :wbs_level, :is_summary, :notes, :site_id, :template_id, :quantity, :unit,
                :planned_start_formula, :planned_finish_formula,
                :planned_start_offset_days, :planned_finish_offset_days)""",
            prepared,
        )


def _get_or_create_template(conn: sqlite3.Connection, project_id: int, task: dict) -> int:
    task_name = str(task.get("task_name", "")).strip()
    if not task_name:
        raise ValueError("task_name is required to map template")

    row = conn.execute(
        "SELECT id FROM task_templates WHERE project_id = ? AND lower(task_name) = lower(?)",
        (project_id, task_name),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE task_templates
               SET default_unit = COALESCE(default_unit, ?),
                   default_duration = COALESCE(default_duration, ?),
                   default_wbs = COALESCE(default_wbs, ?),
                   wbs_level = COALESCE(wbs_level, ?),
                   is_summary = CASE WHEN is_summary = 1 OR ? = 1 THEN 1 ELSE 0 END,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                task.get("unit"),
                task.get("duration_days"),
                task.get("wbs"),
                task.get("wbs_level"),
                int(task.get("is_summary", 0) or 0),
                row["id"],
            ),
        )
        return int(row["id"])

    cur = conn.execute(
        """INSERT INTO task_templates
           (project_id, task_name, default_unit, default_duration, default_wbs, wbs_level, is_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            task_name,
            task.get("unit"),
            task.get("duration_days"),
            task.get("wbs"),
            task.get("wbs_level"),
            int(task.get("is_summary", 0) or 0),
        ),
    )
    return int(cur.lastrowid)


def upsert_site_tasks(
    project_id: int,
    site_id: int,
    tasks: list[dict],
    dependencies: Optional[list[dict]] = None,
) -> None:
    """Replace tasks for a specific site, reusing shared project task templates."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM tasks WHERE project_id = ? AND site_id = ?",
            (project_id, site_id),
        )
        conn.execute(
            "DELETE FROM task_formula_dependencies WHERE project_id = ? AND site_id = ?",
            (project_id, site_id),
        )

        prepared: list[dict] = []
        for task in tasks:
            template_id = _get_or_create_template(conn, project_id, task)
            prepared.append(
                {
                    "project_id": project_id,
                    "row_num": task.get("row_num"),
                    "wbs": task.get("wbs", ""),
                    "task_name": task.get("task_name", ""),
                    "duration_days": task.get("duration_days"),
                    "start_date": task.get("start_date"),
                    "finish_date": task.get("finish_date"),
                    "predecessors": task.get("predecessors", ""),
                    "pct_complete": task.get("pct_complete", 0.0),
                    "wbs_level": task.get("wbs_level", 1),
                    "is_summary": task.get("is_summary", 0),
                    "notes": task.get("notes", ""),
                    "site_id": site_id,
                    "template_id": template_id,
                    "quantity": task.get("quantity"),
                    "unit": task.get("unit"),
                    "planned_start_formula": task.get("planned_start_formula"),
                    "planned_finish_formula": task.get("planned_finish_formula"),
                    "planned_start_offset_days": task.get("planned_start_offset_days"),
                    "planned_finish_offset_days": task.get("planned_finish_offset_days"),
                }
            )

        conn.executemany(
            """INSERT INTO tasks
               (project_id, row_num, wbs, task_name, duration_days,
                start_date, finish_date, predecessors, pct_complete,
                wbs_level, is_summary, notes, site_id, template_id, quantity, unit,
                planned_start_formula, planned_finish_formula,
                planned_start_offset_days, planned_finish_offset_days)
               VALUES
               (:project_id, :row_num, :wbs, :task_name, :duration_days,
                :start_date, :finish_date, :predecessors, :pct_complete,
                :wbs_level, :is_summary, :notes, :site_id, :template_id, :quantity, :unit,
                :planned_start_formula, :planned_finish_formula,
                :planned_start_offset_days, :planned_finish_offset_days)""",
            prepared,
        )

        if dependencies:
            dep_rows = []
            for dep in dependencies:
                dep_rows.append(
                    {
                        "project_id": project_id,
                        "site_id": site_id,
                        "from_row_num": dep.get("from_row_num"),
                        "to_row_num": dep.get("to_row_num"),
                        "dep_type": dep.get("dep_type", "FS"),
                        "lag_days": dep.get("lag_days", 0),
                        "source_formula": dep.get("source_formula"),
                    }
                )

            conn.executemany(
                """INSERT INTO task_formula_dependencies
                   (project_id, site_id, from_row_num, to_row_num, dep_type, lag_days, source_formula)
                   VALUES
                   (:project_id, :site_id, :from_row_num, :to_row_num, :dep_type, :lag_days, :source_formula)""",
                dep_rows,
            )


def recompute_site_task_dates(site_id: int, anchor_start_date: str) -> None:
    """Re-derive task dates from stored relative offsets and the provided site anchor date."""
    with get_conn() as conn:
        conn.execute("UPDATE sites SET anchor_start_date = ? WHERE id = ?", (anchor_start_date, site_id))
        conn.execute(
            """UPDATE tasks
               SET start_date = CASE
                     WHEN planned_start_offset_days IS NOT NULL
                     THEN date(?, printf('%+d day', CAST(planned_start_offset_days AS INTEGER)))
                     ELSE start_date
                   END,
                   finish_date = CASE
                     WHEN planned_finish_offset_days IS NOT NULL
                     THEN date(?, printf('%+d day', CAST(planned_finish_offset_days AS INTEGER)))
                     ELSE finish_date
                   END,
                   updated_at = CURRENT_TIMESTAMP
               WHERE site_id = ?""",
            (anchor_start_date, anchor_start_date, site_id),
        )


def get_site_dependencies(project_id: int, site_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT *
               FROM task_formula_dependencies
               WHERE project_id = ? AND site_id = ?
               ORDER BY to_row_num, from_row_num""",
            (project_id, site_id),
        ).fetchall()
        return [dict(r) for r in rows]


def get_tasks(project_id: int, site_id: Optional[int] = None) -> list[dict]:
    with get_conn() as conn:
        if site_id is not None:
            rows = conn.execute(
                """SELECT t.*, s.name AS site_name
                   FROM tasks t
                   LEFT JOIN sites s ON s.id = t.site_id
                   WHERE t.project_id = ? AND t.site_id = ?
                   ORDER BY t.row_num""",
                (project_id, site_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.*, s.name AS site_name
                   FROM tasks t
                   LEFT JOIN sites s ON s.id = t.site_id
                   WHERE t.project_id = ?
                   ORDER BY t.row_num""",
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
                """SELECT l.*, t.task_name, t.wbs, s.name AS site_name
                   FROM daily_logs l
                   JOIN tasks t ON t.id = l.task_id
                   LEFT JOIN sites s ON s.id = t.site_id
                   WHERE t.project_id = ? AND l.log_date = ?
                   ORDER BY l.created_at DESC""",
                (project_id, log_date),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT l.*, t.task_name, t.wbs, s.name AS site_name
                   FROM daily_logs l
                   JOIN tasks t ON t.id = l.task_id
                   LEFT JOIN sites s ON s.id = t.site_id
                   WHERE t.project_id = ?
                   ORDER BY l.log_date DESC, l.created_at DESC""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_tasks_active_on(project_id: int, on_date: str, site_id: Optional[int] = None) -> list[dict]:
    """Return tasks whose date range includes on_date."""
    with get_conn() as conn:
        if site_id is not None:
            rows = conn.execute(
                """SELECT t.*, s.name AS site_name
                   FROM tasks t
                   LEFT JOIN sites s ON s.id = t.site_id
                   WHERE t.project_id = ?
                     AND t.site_id = ?
                     AND t.start_date <= ?
                     AND t.finish_date >= ?
                     AND t.is_summary = 0
                   ORDER BY t.row_num""",
                (project_id, site_id, on_date, on_date),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.*, s.name AS site_name
                   FROM tasks t
                   LEFT JOIN sites s ON s.id = t.site_id
                   WHERE t.project_id = ?
                     AND t.start_date <= ?
                     AND t.finish_date >= ?
                     AND t.is_summary = 0
                   ORDER BY t.row_num""",
                (project_id, on_date, on_date),
            ).fetchall()
        return [dict(r) for r in rows]
