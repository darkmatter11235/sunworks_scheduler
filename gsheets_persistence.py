"""
Google Sheets persistence adapter for sunworks_scheduler.

This module syncs all SQLite tables to/from a Google Sheet workbook using
Streamlit's connector API. It keeps SQLite as runtime DB and uses Google
Sheets as persistent backing storage across app invocations.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

import db

try:
    from streamlit_gsheets import GSheetsConnection
except Exception:  # pragma: no cover
    GSheetsConnection = None


def _get_connection() -> Optional[object]:
    if GSheetsConnection is None:
        return None
    try:
        return st.connection("gsheets", type=GSheetsConnection)
    except Exception:
        return None


def is_enabled() -> bool:
    return _get_connection() is not None


def pull_into_sqlite() -> bool:
    """Load data from Google Sheets into SQLite. Returns True if imported any rows."""
    conn = _get_connection()
    if conn is None:
        return False

    table_data: dict[str, pd.DataFrame] = {}
    total_rows = 0
    for table in db.TABLE_SYNC_ORDER:
        try:
            df = conn.read(worksheet=table, ttl=0)
        except Exception:
            df = pd.DataFrame()

        if df is None:
            df = pd.DataFrame()

        # Connector may add unnamed index columns.
        df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed:")]]
        table_data[table] = df
        total_rows += len(df.index)

    if total_rows == 0:
        return False

    db.import_all_tables(table_data)
    return True


def push_from_sqlite() -> bool:
    """Write local SQLite data to Google Sheets worksheets."""
    conn = _get_connection()
    if conn is None:
        return False

    exported = db.export_all_tables()
    for table in db.TABLE_SYNC_ORDER:
        df = exported.get(table, pd.DataFrame())
        conn.update(worksheet=table, data=df)
    return True


def bootstrap_storage() -> str:
    """
    Bootstrap data between SQLite and Google Sheets.

    Returns one of:
      - "disabled": connector not configured
      - "pulled": data loaded from Google Sheets into SQLite
      - "pushed": local SQLite pushed to empty Google Sheets workbook
      - "empty": both stores had no data
    """
    conn = _get_connection()
    if conn is None:
        return "disabled"

    imported = pull_into_sqlite()
    if imported:
        return "pulled"

    if db.has_any_data():
        push_from_sqlite()
        return "pushed"

    return "empty"
