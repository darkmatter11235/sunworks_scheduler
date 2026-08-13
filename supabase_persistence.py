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
from time import sleep

import pandas as pd
import requests
import streamlit as st

import db

TABLE_NAME = "scheduler_state"
ROW_ID = 1
REQUEST_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.5

PULL_STATUS_DISABLED = "disabled"
PULL_STATUS_PULLED = "pulled"
PULL_STATUS_EMPTY = "empty"
PULL_STATUS_UNAVAILABLE = "unavailable"

_last_sync_error = ""


def _set_last_sync_error(message: str) -> None:
    global _last_sync_error
    _last_sync_error = message.strip()


def get_last_sync_error() -> str:
    return _last_sync_error


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


def _get_db_url() -> str:
    supa_cfg = st.secrets.get("supabase", {}) if hasattr(st, "secrets") else {}
    db_url = str(supa_cfg.get("db_url", "") or "").strip()
    if not db_url:
        db_url = os.getenv("SUPABASE_DB_URL", "").strip()
    return db_url


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _request_with_retries(method: str, endpoint: str, **kwargs) -> Optional[requests.Response]:
    last_error: Optional[Exception] = None
    for attempt in range(REQUEST_RETRIES):
        try:
            return requests.request(method, endpoint, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt < REQUEST_RETRIES - 1:
                sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

    if last_error:
        _set_last_sync_error(f"Network error: {last_error}")
        return None
    return None


def _is_missing_table_response(resp: requests.Response) -> bool:
    if resp.status_code != 404:
        return False
    body = resp.text or ""
    return "PGRST205" in body and TABLE_NAME in body


def _ensure_remote_table_exists() -> bool:
    db_url = _get_db_url()
    if not db_url:
        _set_last_sync_error(
            "Supabase table missing. Set supabase.db_url (or SUPABASE_DB_URL) to auto-create scheduler_state."
        )
        return False

    try:
        import psycopg
    except Exception:
        _set_last_sync_error(
            "psycopg is required for auto-creating scheduler_state table. Install dependencies and redeploy."
        )
        return False

    ddl = """
    create table if not exists public.scheduler_state (
      id integer primary key,
      payload jsonb not null,
      updated_at timestamptz default now()
    );
    """
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
        return True
    except Exception as exc:
        _set_last_sync_error(f"Failed to auto-create scheduler_state table: {exc}")
        return False


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


def pull_into_sqlite() -> str:
    """Load persisted state from Supabase into SQLite."""
    url, key = _get_credentials()
    if not url or not key:
        _set_last_sync_error("Supabase credentials not configured")
        return PULL_STATUS_DISABLED

    endpoint = f"{url}/{TABLE_NAME}"
    params = {
        "id": f"eq.{ROW_ID}",
        "select": "payload",
    }

    resp = _request_with_retries(
        "GET",
        endpoint,
        headers=_headers(key),
        params=params,
        timeout=20,
    )
    if resp is None:
        return PULL_STATUS_UNAVAILABLE

    if resp.status_code != 200:
        if _is_missing_table_response(resp) and _ensure_remote_table_exists():
            _set_last_sync_error("")
            return PULL_STATUS_EMPTY
        _set_last_sync_error(f"Supabase read failed ({resp.status_code}): {resp.text[:240]}")
        return PULL_STATUS_UNAVAILABLE

    try:
        rows = resp.json()
    except ValueError:
        _set_last_sync_error("Supabase returned invalid JSON payload")
        return PULL_STATUS_UNAVAILABLE

    if not rows:
        _set_last_sync_error("")
        return PULL_STATUS_EMPTY

    payload = rows[0].get("payload")
    if not isinstance(payload, dict):
        _set_last_sync_error("Supabase payload shape is invalid")
        return PULL_STATUS_UNAVAILABLE

    db.import_all_tables(_payload_to_state(payload))
    _set_last_sync_error("")
    return PULL_STATUS_PULLED


def push_from_sqlite() -> bool:
    """Write current SQLite state into Supabase."""
    url, key = _get_credentials()
    if not url or not key:
        _set_last_sync_error("Supabase credentials not configured")
        return False

    endpoint = f"{url}/{TABLE_NAME}?on_conflict=id"
    body = [{"id": ROW_ID, "payload": _state_to_payload()}]
    headers = _headers(key)
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

    resp = _request_with_retries(
        "POST",
        endpoint,
        headers=headers,
        data=json.dumps(body),
        timeout=30,
    )
    if resp is None:
        return False

    # 201/200 for insert/update; 204 may appear depending on proxy behavior.
    if resp.status_code in (200, 201, 204):
        _set_last_sync_error("")
        return True

    if _is_missing_table_response(resp) and _ensure_remote_table_exists():
        retry_resp = _request_with_retries(
            "POST",
            endpoint,
            headers=headers,
            data=json.dumps(body),
            timeout=30,
        )
        if retry_resp is not None and retry_resp.status_code in (200, 201, 204):
            _set_last_sync_error("")
            return True

    _set_last_sync_error(f"Supabase write failed ({resp.status_code}): {resp.text[:240]}")
    return False


def bootstrap_storage() -> str:
    """
    Bootstrap data between SQLite and Supabase.

    Returns one of:
      - "disabled": Supabase credentials not configured
      - "pulled": data loaded from Supabase into SQLite
      - "pushed": local SQLite pushed to Supabase
            - "unavailable": Supabase configured but the remote store could not be read or written
      - "empty": both stores had no data
    """
    if not is_enabled():
        _set_last_sync_error("Supabase credentials not configured")
        return "disabled"

    pull_status = pull_into_sqlite()
    if pull_status == PULL_STATUS_PULLED:
        return "pulled"
    if pull_status == PULL_STATUS_UNAVAILABLE:
        return "unavailable"

    if db.has_any_data():
        pushed = push_from_sqlite()
        return "pushed" if pushed else "unavailable"

    return "empty"
