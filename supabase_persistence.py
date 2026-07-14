"""
Supabase-backed persistence adapter for sunworks_scheduler.

SQLite remains the runtime database. This adapter syncs the full SQLite state
into a single Supabase table row and restores it on startup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import streamlit as st

import db

TABLE_NAME = "scheduler_state"
ROW_ID = 1


def _read_credentials_from_local_file() -> tuple[str, str]:
    """Best-effort local fallback for development only."""
    creds_file = Path(__file__).parent / "supabase credentials"
    if not creds_file.exists():
        return "", ""

    try:
        lines = [ln.strip() for ln in creds_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return "", ""

    if len(lines) < 2:
        return "", ""
    return lines[0], lines[1]


def _get_credentials() -> tuple[str, str]:
    # Preferred: Streamlit secrets
    supa_cfg = st.secrets.get("supabase", {}) if hasattr(st, "secrets") else {}
    url = str(supa_cfg.get("url", "") or "").strip()
    key = str(supa_cfg.get("key", "") or "").strip()

    # Environment fallback
    if not url:
        url = os.getenv("SUPABASE_URL", "").strip()
    if not key:
        key = os.getenv("SUPABASE_KEY", "").strip()

    # Local file fallback (dev only)
    if not url or not key:
        f_url, f_key = _read_credentials_from_local_file()
        url = url or f_url
        key = key or f_key

    return url.rstrip("/"), key


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def is_enabled() -> bool:
    url, key = _get_credentials()
    return bool(url and key)


def _state_to_payload() -> dict:
    tables = db.export_all_tables()
    payload_tables: dict[str, list[dict]] = {}
    for table in db.TABLE_SYNC_ORDER:
        df = tables.get(table, pd.DataFrame())
        payload_tables[table] = json.loads(df.to_json(orient="records", date_format="iso"))
    return {"tables": payload_tables}


def _payload_to_state(payload: dict) -> dict[str, pd.DataFrame]:
    tables_obj = payload.get("tables", {}) if isinstance(payload, dict) else {}
    out: dict[str, pd.DataFrame] = {}
    for table in db.TABLE_SYNC_ORDER:
        rows = tables_obj.get(table, [])
        out[table] = pd.DataFrame(rows if isinstance(rows, list) else [])
    return out


def pull_into_sqlite() -> bool:
    """Load persisted state from Supabase into SQLite."""
    url, key = _get_credentials()
    if not url or not key:
        return False

    endpoint = f"{url}/{TABLE_NAME}"
    params = {
        "id": f"eq.{ROW_ID}",
        "select": "payload",
    }

    try:
        resp = requests.get(endpoint, headers=_headers(key), params=params, timeout=20)
    except Exception:
        return False

    if resp.status_code != 200:
        return False

    try:
        rows = resp.json()
    except ValueError:
        return False

    if not rows:
        return False

    payload = rows[0].get("payload")
    if not isinstance(payload, dict):
        return False

    db.import_all_tables(_payload_to_state(payload))
    return True


def push_from_sqlite() -> bool:
    """Write current SQLite state into Supabase."""
    url, key = _get_credentials()
    if not url or not key:
        return False

    endpoint = f"{url}/{TABLE_NAME}?on_conflict=id"
    body = [{"id": ROW_ID, "payload": _state_to_payload()}]
    headers = _headers(key)
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

    try:
        resp = requests.post(endpoint, headers=headers, data=json.dumps(body), timeout=30)
    except Exception:
        return False

    # 201/200 for insert/update; 204 may appear depending on proxy behavior.
    return resp.status_code in (200, 201, 204)


def bootstrap_storage() -> str:
    """
    Bootstrap data between SQLite and Supabase.

    Returns one of:
      - "disabled": Supabase credentials not configured
      - "pulled": data loaded from Supabase into SQLite
      - "pushed": local SQLite pushed to Supabase
      - "unavailable": Supabase configured but sync table/write not available
      - "empty": both stores had no data
    """
    if not is_enabled():
        return "disabled"

    imported = pull_into_sqlite()
    if imported:
        return "pulled"

    if db.has_any_data():
        pushed = push_from_sqlite()
        return "pushed" if pushed else "unavailable"

    return "empty"
