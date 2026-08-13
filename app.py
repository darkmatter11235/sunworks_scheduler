#!/usr/bin/env python3
"""
sunworks_scheduler — Streamlit Gantt & Schedule Tracker
Run with:  streamlit run app.py
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, datetime, timedelta
from pathlib import Path
from io import BytesIO, StringIO

import db
import loader
import supabase_persistence

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Sunworks Scheduler",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── DB init ─────────────────────────────────────────────────────────────────
db.init_db()

# Optional persistent backing store via Supabase.
if "storage_bootstrapped" not in st.session_state:
    st.session_state.storage_bootstrapped = True
    st.session_state.storage_status = supabase_persistence.bootstrap_storage()

# ─── Session state ────────────────────────────────────────────────────────────
for key, default in [
    ("project_id", None),
    ("site_id", None),
    ("tasks_df", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ─── Helpers ─────────────────────────────────────────────────────────────────
WBS_COLORS = px.colors.qualitative.Plotly  # 10-color cycle


def _load_tasks(project_id: int, site_id: int | None = None) -> pd.DataFrame:
    rows = db.get_tasks(project_id, site_id=site_id)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["finish_date"] = pd.to_datetime(df["finish_date"], errors="coerce")
    df["pct_complete"] = df["pct_complete"].fillna(0).astype(float)
    return df


def _wbs_phase(wbs: str) -> str:
    """Return top-level phase from WBS string, e.g. '2.3.1' → '2'."""
    if not wbs or str(wbs).strip() == "":
        return "—"
    return str(wbs).strip().split(".")[0]


def _color_for_phase(phase: str, phases: list[str]) -> str:
    try:
        idx = phases.index(phase) % len(WBS_COLORS)
        return WBS_COLORS[idx]
    except ValueError:
        return "#cccccc"


def _gantt_figure(df: pd.DataFrame, today: date, height: int = 700) -> go.Figure:
    """Build a Plotly Gantt figure from the tasks dataframe."""
    gdf = df.dropna(subset=["start_date", "finish_date"]).copy()
    gdf = gdf[gdf["finish_date"] >= gdf["start_date"]]
    if gdf.empty:
        fig = go.Figure()
        fig.update_layout(title="No tasks with valid dates to display")
        return fig

    gdf["phase"] = gdf["wbs"].fillna("").apply(_wbs_phase)
    phases = sorted(gdf["phase"].unique().tolist())

    gdf["color"] = gdf["phase"].apply(lambda p: _color_for_phase(p, phases))
    gdf["label"] = gdf["wbs"].fillna("") + "  " + gdf["task_name"]
    gdf["pct_label"] = gdf["pct_complete"].apply(lambda v: f"{v:.0f}%")

    fig = px.timeline(
        gdf,
        x_start="start_date",
        x_end="finish_date",
        y="label",
        color="phase",
        color_discrete_sequence=WBS_COLORS,
        custom_data=["task_name", "wbs", "duration_days", "pct_complete", "predecessors"],
        title="Project Gantt Chart",
    )

    fig.update_traces(
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "WBS: %{customdata[1]}<br>"
            "Duration: %{customdata[2]} days<br>"
            "Progress: %{customdata[3]:.0f}%<br>"
            "Predecessors: %{customdata[4]}<extra></extra>"
        )
    )

    # Progress overlay — thin darker bar showing completion
    for _, row in gdf.iterrows():
        if row["pct_complete"] > 0 and pd.notna(row["start_date"]) and pd.notna(row["finish_date"]):
            total_td = row["finish_date"] - row["start_date"]
            done_td = total_td * (row["pct_complete"] / 100)
            done_end = row["start_date"] + done_td
            fig.add_shape(
                type="rect",
                x0=row["start_date"],
                x1=done_end,
                y0=row["label"],
                y1=row["label"],
                xref="x",
                yref="y",
                line=dict(color="rgba(0,0,0,0.6)", width=6),
            )

    # Today line
    fig.add_vline(
        x=pd.Timestamp(today),
        line_dash="dash",
        line_color="red",
    )
    fig.add_annotation(
        x=pd.Timestamp(today),
        y=1,
        yref="paper",
        text="Today",
        showarrow=False,
        font=dict(color="red"),
        xanchor="left",
        yanchor="top",
    )

    fig.update_yaxes(autorange="reversed", title="")
    fig.update_xaxes(
        title="Date",
        range=[
            gdf["start_date"].min() - pd.Timedelta(days=3),
            gdf["finish_date"].max() + pd.Timedelta(days=3),
        ],
    )
    fig.update_layout(
        height=height,
        legend_title="Phase",
        margin=dict(l=10, r=10, t=50, b=10),
        plot_bgcolor="#f9f9f9",
    )
    return fig


def _progress_donut(pct: float) -> go.Figure:
    fig = go.Figure(go.Pie(
        values=[pct, 100 - pct],
        hole=0.7,
        marker_colors=["#2ecc71", "#ecf0f1"],
        textinfo="none",
        hoverinfo="skip",
    ))
    fig.update_layout(
        showlegend=False,
        margin=dict(l=0, r=0, t=0, b=0),
        annotations=[dict(
            text=f"{pct:.0f}%",
            x=0.5, y=0.5,
            font_size=20,
            showarrow=False,
        )],
        height=150,
    )
    return fig


def _flush_persistent_storage() -> None:
    """Push local SQLite state to Supabase when configured."""
    if supabase_persistence.is_enabled():
        supabase_persistence.push_from_sqlite()


# ─── Sidebar — project management ────────────────────────────────────────────
with st.sidebar:
    st.title("📅 Sunworks Scheduler")
    st.divider()

    st.subheader("Projects")
    if st.session_state.get("storage_status") == "pulled":
        st.caption("Storage: Supabase synced")
    elif st.session_state.get("storage_status") == "pushed":
        st.caption("Storage: Supabase initialized from local DB")
    elif st.session_state.get("storage_status") == "unavailable":
        st.caption("Storage: Supabase unavailable, using current local DB state")
    elif supabase_persistence.is_enabled():
        st.caption("Storage: Supabase enabled")
    else:
        st.caption("Storage: local SQLite only")

    projects = db.list_projects()
    project_names = [p["name"] for p in projects]
    project_ids = [p["id"] for p in projects]

    if projects:
        sel_name = st.selectbox("Select project", project_names)
        sel_id = project_ids[project_names.index(sel_name)]
        if st.session_state.project_id != sel_id:
            st.session_state.project_id = sel_id
            st.session_state.site_id = None

        project_sites = db.list_sites(st.session_state.project_id)
        if project_sites:
            site_names = [s["name"] for s in project_sites]
            site_ids = [s["id"] for s in project_sites]

            if st.session_state.site_id not in site_ids:
                st.session_state.site_id = site_ids[0]

            selected_site_name = st.selectbox(
                "Select site",
                site_names,
                index=site_ids.index(st.session_state.site_id),
            )
            st.session_state.site_id = site_ids[site_names.index(selected_site_name)]

            st.session_state.tasks_df = _load_tasks(
                sel_id,
                site_id=st.session_state.site_id,
            )
        else:
            st.session_state.site_id = None
            st.session_state.tasks_df = _load_tasks(sel_id, site_id=None)
    else:
        st.info("No projects yet. Import a schedule below.")

    st.divider()

    # ── Create new project ──
    with st.expander("➕ New project"):
        new_name = st.text_input("Project name", key="new_project_name")
        new_desc = st.text_area("Description", key="new_project_desc", height=60)
        if st.button("Create project"):
            if new_name.strip():
                pid = db.create_project(new_name.strip(), new_desc.strip())
                _flush_persistent_storage()
                st.session_state.project_id = pid
                st.session_state.site_id = None
                st.session_state.tasks_df = pd.DataFrame()
                st.success(f"Created project '{new_name}'")
                st.rerun()
            else:
                st.warning("Enter a project name.")

    with st.expander("🏗️ Sites"):
        if st.session_state.project_id is None:
            st.warning("Create or select a project first.")
        else:
            site_name = st.text_input("New site name", key="new_site_name")
            site_desc = st.text_area("Site description", key="new_site_desc", height=60)
            if st.button("Create site"):
                if site_name.strip():
                    sid = db.create_site(st.session_state.project_id, site_name.strip(), site_desc.strip())
                    _flush_persistent_storage()
                    st.session_state.site_id = sid
                    st.session_state.tasks_df = _load_tasks(st.session_state.project_id, sid)
                    st.success(f"Ready for site '{site_name.strip()}'")
                    st.rerun()
                else:
                    st.warning("Enter a site name.")

            if st.session_state.site_id is not None:
                st.divider()
                site_row = db.get_site(st.session_state.site_id) or {}
                anchor_val = site_row.get("anchor_start_date")
                if anchor_val:
                    if isinstance(anchor_val, datetime):
                        anchor_default = anchor_val.date()
                    elif isinstance(anchor_val, date):
                        anchor_default = anchor_val
                    else:
                        try:
                            anchor_default = datetime.strptime(str(anchor_val), "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            anchor_default = date.today()
                else:
                    anchor_default = date.today()

                anchor_input = st.date_input("Site anchor start", value=anchor_default, key="site_anchor_start")
                if st.button("Recalculate planned dates"):
                    db.recompute_site_task_dates(st.session_state.site_id, str(anchor_input))
                    _flush_persistent_storage()
                    st.session_state.tasks_df = _load_tasks(
                        st.session_state.project_id,
                        site_id=st.session_state.site_id,
                    )
                    st.success("Recalculated planned dates from relative offsets.")
                    st.rerun()

    # ── Import schedule ──
    with st.expander("📥 Import schedule"):
        if st.session_state.project_id is None:
            st.warning("Create or select a project first.")
        else:
            import_mode = st.radio(
                "Import mode",
                ["Site quantity schedule", "Relative formula schedule (Excel)", "MS Project schedule"],
                horizontal=True,
                help="Use relative formula mode for formula-driven planned start/finish in Excel.",
            )

            import_site_name = st.text_input(
                "Site name for import",
                value=(db.get_site(st.session_state.site_id) or {}).get("name", "")
                if st.session_state.site_id is not None else "",
                key="import_site_name",
            )

            uploaded = st.file_uploader(
                "Upload CSV or Excel",
                type=["csv", "xlsx", "xls"],
                key="schedule_upload",
            )

            if uploaded is not None:
                try:
                    raw = uploaded.read()
                    imported_count = 0
                    if import_mode == "Site quantity schedule":
                        if not import_site_name.strip():
                            raise ValueError("Site name is required for site quantity import.")

                        site_id = db.create_site(st.session_state.project_id, import_site_name.strip())
                        st.session_state.site_id = site_id

                        if uploaded.name.endswith(".csv"):
                            tasks = loader.parse_site_quantity_csv(
                                StringIO(raw.decode("utf-8", errors="replace")),
                                st.session_state.project_id,
                            )
                        else:
                            # Reuse CSV parser by converting Excel sheet to CSV in-memory.
                            xl_df = pd.read_excel(BytesIO(raw), dtype=str)
                            buf = StringIO()
                            xl_df.to_csv(buf, index=False)
                            buf.seek(0)
                            tasks = loader.parse_site_quantity_csv(
                                buf,
                                st.session_state.project_id,
                            )

                        db.upsert_site_tasks(st.session_state.project_id, site_id, tasks)
                        _flush_persistent_storage()
                        st.session_state.tasks_df = _load_tasks(st.session_state.project_id, site_id)
                        imported_count = len(tasks)
                    elif import_mode == "Relative formula schedule (Excel)":
                        if not import_site_name.strip():
                            raise ValueError("Site name is required for relative formula import.")
                        if not uploaded.name.endswith((".xlsx", ".xls")):
                            raise ValueError("Relative formula import expects an Excel file (.xlsx/.xls).")

                        site_id = db.create_site(st.session_state.project_id, import_site_name.strip())
                        st.session_state.site_id = site_id

                        parsed = loader.parse_relative_formula_excel(
                            BytesIO(raw),
                            st.session_state.project_id,
                        )
                        tasks = parsed["tasks"]
                        db.upsert_site_tasks(
                            st.session_state.project_id,
                            site_id,
                            tasks,
                            dependencies=parsed["dependencies"],
                        )
                        db.set_site_anchor_start(site_id, parsed["anchor_start_date"])
                        _flush_persistent_storage()
                        st.session_state.tasks_df = _load_tasks(st.session_state.project_id, site_id)
                        imported_count = len(tasks)
                    else:
                        if uploaded.name.endswith(".csv"):
                            tasks = loader.parse_schedule_csv(
                                StringIO(raw.decode("utf-8", errors="replace")),
                                st.session_state.project_id,
                            )
                        else:
                            tasks = loader.parse_schedule_excel(
                                BytesIO(raw),
                                st.session_state.project_id,
                            )
                        db.upsert_tasks(st.session_state.project_id, tasks)
                        _flush_persistent_storage()
                        st.session_state.site_id = None
                        st.session_state.tasks_df = _load_tasks(st.session_state.project_id)
                        imported_count = len(tasks)

                    st.success(f"Imported {imported_count} tasks.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Import failed: {exc}")

    # ── Delete project ──
    if st.session_state.project_id:
        with st.expander("🗑 Delete project"):
            st.warning("This will delete all tasks and logs for this project.")
            if st.button("Confirm delete", type="primary"):
                db.delete_project(st.session_state.project_id)
                _flush_persistent_storage()
                st.session_state.project_id = None
                st.session_state.site_id = None
                st.session_state.tasks_df = None
                st.rerun()


# ─── Main area ────────────────────────────────────────────────────────────────
if st.session_state.project_id is None:
    st.title("📅 Sunworks Scheduler")
    st.info("Create or select a project in the sidebar to get started.")
    st.stop()

df = st.session_state.tasks_df
if df is None or df.empty:
    st.title("📅 Sunworks Scheduler")
    st.info("Import a project schedule (CSV or Excel) from the sidebar.")
    st.stop()

today = date.today()
proj = db.get_project(st.session_state.project_id)

st.title(f"📅 {proj['name']}")
if proj.get("description"):
    st.caption(proj["description"])

# ─── KPI row ──────────────────────────────────────────────────────────────────
leaf_df = df[df["is_summary"] == 0].copy()
total_tasks = len(leaf_df)
completed = int((leaf_df["pct_complete"] >= 100).sum())
in_progress = int(((leaf_df["pct_complete"] > 0) & (leaf_df["pct_complete"] < 100)).sum())
not_started = total_tasks - completed - in_progress
overall_pct = leaf_df["pct_complete"].mean() if total_tasks > 0 else 0.0

kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
kpi1.metric("Total tasks", total_tasks)
kpi2.metric("Completed", completed, help="pct_complete = 100")
kpi3.metric("In Progress", in_progress)
kpi4.metric("Not Started", not_started)
with kpi5:
    st.plotly_chart(_progress_donut(overall_pct), use_container_width=True, config={"displayModeBar": False})

st.divider()

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab_gantt, tab_today, tab_update, tab_log = st.tabs(
    ["📊 Gantt Chart", "📋 Today's Tasks", "✏️ Update Progress", "📜 Activity Log"]
)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Gantt Chart
# ══════════════════════════════════════════════════════════════════════════════
with tab_gantt:
    col_f1, col_f2, col_f3 = st.columns([2, 2, 1])

    with col_f1:
        all_phases = sorted(df["wbs"].fillna("").apply(_wbs_phase).unique().tolist())
        sel_phases = st.multiselect("Filter by Phase (WBS level 1)", all_phases, default=all_phases)

    with col_f2:
        date_min = df["start_date"].min()
        date_max = df["finish_date"].max()
        if pd.notna(date_min) and pd.notna(date_max):
            date_range = st.date_input(
                "Date window",
                value=(date_min.date(), date_max.date()),
                min_value=date_min.date(),
                max_value=date_max.date(),
            )
        else:
            date_range = None

    with col_f3:
        show_summary = st.checkbox("Show summary rows", value=False)
        gantt_height = st.slider("Chart height", 400, 2000, 700, 100)

    # Apply filters
    gdf = df.copy()
    gdf["phase"] = gdf["wbs"].fillna("").apply(_wbs_phase)
    if sel_phases:
        gdf = gdf[gdf["phase"].isin(sel_phases)]
    if not show_summary:
        gdf = gdf[gdf["is_summary"] == 0]
    if date_range and len(date_range) == 2:
        dr_start = pd.Timestamp(date_range[0])
        dr_end = pd.Timestamp(date_range[1])
        gdf = gdf[
            (gdf["finish_date"] >= dr_start) & (gdf["start_date"] <= dr_end)
        ]

    st.plotly_chart(
        _gantt_figure(gdf, today, height=gantt_height),
        use_container_width=True,
    )

    with st.expander("📋 Task table"):
        display_cols = ["row_num", "wbs", "task_name", "duration_days",
                        "quantity", "unit", "start_date", "finish_date", "predecessors", "pct_complete"]
        st.dataframe(
            gdf[[c for c in display_cols if c in gdf.columns]],
            use_container_width=True,
            hide_index=True,
        )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Today's Tasks
# ══════════════════════════════════════════════════════════════════════════════
with tab_today:
    selected_day = st.date_input("View tasks for date", value=today, key="today_date")
    active = db.get_tasks_active_on(
        st.session_state.project_id,
        str(selected_day),
        site_id=st.session_state.site_id,
    )

    if not active:
        st.info(f"No active leaf tasks on {selected_day}.")
    else:
        st.subheader(f"{len(active)} active task(s) on {selected_day}")
        for t in active:
            pct = t["pct_complete"] or 0
            color = "#2ecc71" if pct >= 100 else ("#f39c12" if pct > 0 else "#e74c3c")
            with st.container(border=True):
                c1, c2, c3 = st.columns([4, 1, 1])
                qty_text = ""
                if t.get("quantity") is not None:
                    qty_text = f" | Qty: {t['quantity']} {t.get('unit') or ''}".strip()
                c1.markdown(f"**{t['wbs']}** — {t['task_name']} {qty_text}")
                c2.markdown(f"<span style='color:{color}; font-weight:bold'>{pct:.0f}%</span>",
                            unsafe_allow_html=True)
                c3.caption(f"{t['start_date']} → {t['finish_date']}")
                st.progress(int(pct))


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Update Progress
# ══════════════════════════════════════════════════════════════════════════════
with tab_update:
    st.subheader("Update task progress")

    leaf_tasks = df[df["is_summary"] == 0].copy()
    if leaf_tasks.empty:
        st.info("No leaf tasks found.")
    else:
        search = st.text_input("Search task name or WBS", key="task_search")
        if search:
            mask = (
                leaf_tasks["task_name"].str.contains(search, case=False, na=False) |
                leaf_tasks["wbs"].str.contains(search, case=False, na=False)
            )
            leaf_tasks = leaf_tasks[mask]

        task_labels = [
            f"{row['wbs']}  {row['task_name']}"
            for _, row in leaf_tasks.iterrows()
        ]
        task_ids = leaf_tasks["id"].tolist()

        if not task_labels:
            st.info("No matching tasks.")
        else:
            sel_label = st.selectbox("Select task", task_labels, key="update_task_sel")
            sel_idx = task_labels.index(sel_label)
            sel_task_id = task_ids[sel_idx]
            sel_row = leaf_tasks.iloc[sel_idx]

            st.caption(
                f"Start: {sel_row['start_date'].date() if pd.notna(sel_row['start_date']) else '—'}  |  "
                f"Finish: {sel_row['finish_date'].date() if pd.notna(sel_row['finish_date']) else '—'}  |  "
                f"Duration: {sel_row['duration_days']} days  |  "
                f"Quantity: {sel_row.get('quantity', '—')} {sel_row.get('unit', '')}"
            )

            col_a, col_b = st.columns([2, 1])
            with col_a:
                new_pct = st.slider(
                    "% Complete",
                    0, 100,
                    int(sel_row["pct_complete"]),
                    key="pct_slider",
                )
                comment = st.text_input("Comment / remark", key="update_comment")
            with col_b:
                st.plotly_chart(
                    _progress_donut(new_pct),
                    use_container_width=True,
                    config={"displayModeBar": False},
                )

            if st.button("💾 Save progress", type="primary"):
                db.update_task_progress(sel_task_id, float(new_pct), comment)
                _flush_persistent_storage()
                st.session_state.tasks_df = _load_tasks(
                    st.session_state.project_id,
                    site_id=st.session_state.site_id,
                )
                st.success(f"Updated '{sel_row['task_name']}' to {new_pct}%")
                st.rerun()

            # Date adjustment
            with st.expander("📆 Adjust task dates"):
                new_start = st.date_input(
                    "New start date",
                    value=sel_row["start_date"].date() if pd.notna(sel_row["start_date"]) else today,
                    key="new_start",
                )
                new_finish = st.date_input(
                    "New finish date",
                    value=sel_row["finish_date"].date() if pd.notna(sel_row["finish_date"]) else today,
                    key="new_finish",
                )
                if st.button("💾 Update dates"):
                    if new_finish >= new_start:
                        db.update_task_dates(sel_task_id, str(new_start), str(new_finish))
                        _flush_persistent_storage()
                        st.session_state.tasks_df = _load_tasks(
                            st.session_state.project_id,
                            site_id=st.session_state.site_id,
                        )
                        st.success("Dates updated.")
                        st.rerun()
                    else:
                        st.error("Finish date must be ≥ start date.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Activity Log
# ══════════════════════════════════════════════════════════════════════════════
with tab_log:
    st.subheader("Activity log")

    col_l1, col_l2 = st.columns([1, 3])
    with col_l1:
        log_date_filter = st.date_input("Filter by date (leave blank for all)", value=None, key="log_date")

    logs = db.get_daily_logs(
        st.session_state.project_id,
        str(log_date_filter) if log_date_filter else None,
    )

    if not logs:
        st.info("No activity logged yet.")
    else:
        log_df = pd.DataFrame(logs)
        log_df["change"] = (log_df["pct_after"] - log_df["pct_before"]).apply(
            lambda v: f"+{v:.0f}%" if v >= 0 else f"{v:.0f}%"
        )
        display = log_df[["log_date", "wbs", "task_name", "pct_before",
                           "pct_after", "change", "comment", "logged_by", "site_name", "created_at"]]
        st.dataframe(display, use_container_width=True, hide_index=True)

        # Download
        csv_bytes = log_df.to_csv(index=False).encode()
        st.download_button("⬇ Download log CSV", csv_bytes, "activity_log.csv", "text/csv")
