"""
CSV / Excel loader for project schedule data.

Expected CSV columns (MS-Project export style):
  row_num, WBS, Task Name, Duration, Start, Finish, Predecessors, % Complete

The loader normalises dates, infers WBS depth, flags summary rows,
then returns a list of dicts ready for db.upsert_tasks().
"""
import re
import pandas as pd
from io import StringIO, BytesIO
from typing import Union
from pathlib import Path

# ─── Date parsing ─────────────────────────────────────────────────────────────
_DATE_FMTS = [
    "%a %m/%d/%y %I:%M %p",   # Mon 10/16/17 8:00 AM
    "%m/%d/%y",
    "%m/%d/%Y",
    "%Y-%m-%d",
]


def _parse_date(raw) -> str | None:
    if pd.isna(raw) or str(raw).strip() == "":
        return None
    s = str(raw).strip()
    for fmt in _DATE_FMTS:
        try:
            return pd.to_datetime(s, format=fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return pd.to_datetime(s, infer_datetime_format=True).strftime("%Y-%m-%d")
    except Exception:
        return None


# ─── Duration parsing ─────────────────────────────────────────────────────────
def _parse_duration(raw) -> float | None:
    if pd.isna(raw) or str(raw).strip() == "":
        return None
    s = str(raw).strip().lower().replace("?", "")
    m = re.match(r"([\d.]+)\s*(day|days|d|hr|hrs|hour|hours|h|wk|week|weeks|w)?", s)
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "day").lower()
    if unit.startswith("h"):
        val /= 8
    elif unit.startswith("w"):
        val *= 5
    return round(val, 2)


# ─── WBS helpers ─────────────────────────────────────────────────────────────
def _wbs_level(wbs: str) -> int:
    if not wbs or str(wbs).strip() == "":
        return 1
    return len(str(wbs).strip().split("."))


def _is_summary(wbs: str, task_name: str, duration: float | None) -> int:
    """Heuristic: a row is a summary if its WBS has <= 2 components OR duration is 0."""
    level = _wbs_level(wbs)
    if level <= 2:
        return 1
    if duration is not None and duration <= 0:
        return 1
    return 0


# ─── Main parse function ──────────────────────────────────────────────────────
def parse_schedule_csv(
    source: Union[str, Path, BytesIO, StringIO],
    project_id: int,
) -> list[dict]:
    """
    Parse a project schedule CSV and return a list of task dicts.

    Accepts a file path, a pathlib.Path, or a file-like object (BytesIO / StringIO).
    """
    if isinstance(source, (str, Path)):
        raw = pd.read_csv(source, header=None, dtype=str)
    else:
        raw = pd.read_csv(source, header=None, dtype=str)

    # The CSV has a leading empty column; find the header row
    # Strategy: first non-empty row that contains "Task Name"
    header_row_idx = None
    for i, row in raw.iterrows():
        if any("task name" in str(v).lower() for v in row.values):
            header_row_idx = i
            break

    if header_row_idx is None:
        # Treat first row as header
        header_row_idx = 0

    df = pd.read_csv(
        source if not isinstance(source, (str, Path)) else source,
        skiprows=header_row_idx,
        dtype=str,
    ) if header_row_idx > 0 else pd.read_csv(source, dtype=str)

    # Normalise column names
    df.columns = [str(c).strip().lower().replace(" ", "_").replace("%", "pct") for c in df.columns]

    # Map known column aliases
    _aliases = {
        "task_name": ["task_name", "name", "task"],
        "wbs": ["wbs"],
        "duration": ["duration", "dur"],
        "start": ["start", "start_date"],
        "finish": ["finish", "finish_date", "end", "end_date"],
        "predecessors": ["predecessors", "predecessor", "pred"],
        "pct_complete": ["pct_complete", "_%_complete", "complete", "done"],
    }

    col_map = {}
    for canonical, aliases in _aliases.items():
        for alias in aliases:
            if alias in df.columns:
                col_map[alias] = canonical
                break

    df = df.rename(columns=col_map)

    required = ["task_name"]
    for c in required:
        if c not in df.columns:
            raise ValueError(f"Could not find required column '{c}' in CSV. Columns found: {list(df.columns)}")

    # Fill missing optional columns
    for col, default in [("wbs", ""), ("duration", None), ("start", None),
                         ("finish", None), ("predecessors", ""), ("pct_complete", "0")]:
        if col not in df.columns:
            df[col] = default

    tasks = []
    for idx, row in df.iterrows():
        task_name = str(row.get("task_name", "")).strip()
        if not task_name or task_name.lower() in ("nan", "task name", ""):
            continue

        wbs = str(row.get("wbs", "")).strip()
        dur = _parse_duration(row.get("duration"))
        start = _parse_date(row.get("start"))
        finish = _parse_date(row.get("finish"))
        preds = str(row.get("predecessors", "")).strip()
        pct_raw = str(row.get("pct_complete", "0")).strip().replace("%", "").replace("?", "")
        try:
            pct = float(pct_raw) if pct_raw not in ("", "nan") else 0.0
        except ValueError:
            pct = 0.0

        # row_num: use the leading integer column if present (first col)
        row_num_val = None
        first_col = df.columns[0] if len(df.columns) > 0 else None
        if first_col and first_col not in col_map.values():
            try:
                row_num_val = int(float(str(row[first_col]).strip()))
            except (ValueError, TypeError):
                row_num_val = idx + 1
        else:
            row_num_val = idx + 1

        tasks.append(
            {
                "project_id": project_id,
                "row_num": row_num_val,
                "wbs": wbs if wbs != "nan" else "",
                "task_name": task_name,
                "duration_days": dur,
                "start_date": start,
                "finish_date": finish,
                "predecessors": preds if preds != "nan" else "",
                "pct_complete": pct,
                "wbs_level": _wbs_level(wbs),
                "is_summary": _is_summary(wbs, task_name, dur),
                "notes": "",
            }
        )

    return tasks


def parse_schedule_excel(file_obj: BytesIO, project_id: int, sheet: int | str = 0) -> list[dict]:
    """Parse an Excel workbook (same column structure as CSV)."""
    df = pd.read_excel(file_obj, sheet_name=sheet, dtype=str)
    # Write to an in-memory CSV buffer and reuse csv parser
    buf = StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return parse_schedule_csv(buf, project_id)
