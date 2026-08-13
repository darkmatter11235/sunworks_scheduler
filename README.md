# sunworks_scheduler

Streamlit-based project schedule tracker and Gantt chart visualiser for solar projects in SunWorks.
Built on the same stack as **sunworks_hybrid_optimizer** (Streamlit + Plotly + Pandas + SQLite).

---

## Overview

This application is designed to track and visualize solar project schedules within the SunWorks ecosystem. It provides tools for managing project timelines, dependencies, and progress updates specifically tailored for solar energy projects.

---

## Features

| Feature | Detail |
|---|---|
| Multi-project support | Create and switch between independent projects |
| Multi-site SQL model | Each project can have many sites, each site reuses shared task templates with site-specific quantities |
| Relative planning engine | Planned task dates can be derived from formula dependencies and stored as offsets from site anchor start |
| CSV / Excel import | Supports both MS-Project style schedules and Dakansy-style ACTIVITY/QUANTITY schedules |
| Interactive Gantt | Colour-coded by WBS phase, progress overlay, today marker, date window filter |
| Today's tasks | Daily view of every leaf task active on a selected date |
| Progress updates | Slider update + date adjustment; every change logged with timestamp |
| Activity log | Full audit trail; filterable by date; downloadable as CSV |
| SQLite DB | Zero-configuration, file-based, WAL mode for safe concurrent writes |

---

## Quickstart

```bash
# 1. Clone / enter the repo
cd sunworks_scheduler

# 2. Create a virtual environment (optional but recommended)
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch the app
streamlit run app.py
```

The app opens at `http://localhost:8501`.

## Persistent storage options

By default, data is stored in local SQLite (`scheduler.db`).

For persistence across website invocations (for example in hosted Streamlit),
configure Supabase credentials and the app will automatically use Supabase
as the durable backing store while keeping SQLite as runtime DB.

Create `.streamlit/secrets.toml`:

```toml
[supabase]
url = "https://<YOUR_PROJECT>.supabase.co/rest/v1"
key = "<YOUR_SUPABASE_KEY>"
```

On startup, the app bootstraps storage:

- If Supabase has data, it pulls into SQLite.
- If Supabase is empty and local SQLite has data, it pushes SQLite data to Supabase.
- If Supabase is temporarily unreachable, the app keeps the current local SQLite state instead of treating the remote store as empty.
- On each write action (import, edits, progress updates), it pushes current SQLite tables back to Supabase.

Create this table in Supabase SQL editor:

```sql
create table if not exists scheduler_state (
  id integer primary key,
  payload jsonb not null,
  updated_at timestamptz default now()
);
```

---

## Importing a schedule

1. Click **➕ New project** in the sidebar and give it a name.
2. (Recommended) create a site in **🏗️ Sites**.
3. Click **📥 Import schedule**, pick the import mode, then upload your CSV or Excel file.

The imported schedule is saved to `scheduler.db` and remains available across app restarts.

### Site quantity schedule mode (new)

Use this for Dakansy-style files where each row has an activity and quantity for a site.

Typical columns:

```
S.NO., ACTIVITY, QUANTITY, UNIT, PLANNED START, PLANNED DURATION, PLANNED FINISH,
ACTUAL START, ACTUAL DURATION, ACTUAL FINISH, PERCENT COMPLETE
```

In this mode:

- A **site** is created/selected per import.
- Activities are stored in shared **task_templates** at project level.
- Imported rows become per-site task instances in **tasks** with `quantity` and `unit`.

### Relative formula schedule mode (Excel)

Use this for formula-driven sheets (like `Relative_Formula_Schedule.xlsx`) where planned timing is calculated from dependencies.

In this mode:

- Planned formulas from Excel are persisted per task (`planned_start_formula`, `planned_finish_formula`).
- Dependencies are extracted from formula references (`E` => SS, `G` => FS with lag) into `task_formula_dependencies`.
- Each task stores computed relative offsets (`planned_start_offset_days`, `planned_finish_offset_days`) from the first task start (site anchor).
- The selected site stores `anchor_start_date`; changing anchor in UI recalculates all planned dates from stored offsets.

### Supported CSV format

Columns (order doesn't matter; extra columns are ignored):

```
WBS, Task Name, Duration, Start, Finish, Predecessors, % Complete
```

- **Duration** — e.g. `10 days`, `10d`, `3 wks`
- **Start / Finish** — most common date formats parsed automatically, including `Mon 10/16/17 8:00 AM`
- **Predecessors** — raw MS-Project predecessor string (stored for reference; dependency lines not yet drawn on chart)

A sample file is included at `data/Project Schedule.csv`.

---

## Project structure

```
app.py          — Streamlit UI (Gantt, Today view, Progress update, Log)
db.py           — SQLite CRUD layer
loader.py       — CSV / Excel parser and normaliser
requirements.txt
data/
  Project Schedule.csv   — sample schedule
scheduler.db    — created automatically on first run (gitignored)
```

---

## Database schema

```
projects        id, name, description, created_at, is_active
sites           id, project_id, name, description, anchor_start_date,
                created_at, is_active
task_templates  id, project_id, task_name, default_unit, default_duration,
                default_wbs, wbs_level, is_summary, created_at, updated_at
tasks           id, project_id, row_num, wbs, task_name, duration_days,
                start_date, finish_date, predecessors, pct_complete,
                wbs_level, is_summary, notes, updated_at,
                site_id, template_id, quantity, unit,
                planned_start_formula, planned_finish_formula,
                planned_start_offset_days, planned_finish_offset_days
task_formula_dependencies
                id, project_id, site_id, from_row_num, to_row_num,
                dep_type, lag_days, source_formula
daily_logs      id, task_id, log_date, pct_before, pct_after, comment,
                logged_by, created_at
task_dependencies  id, project_id, from_row_num, to_row_num, dep_type, lag_days
```

---

## Roadmap

- [ ] Dependency arrows on Gantt chart
- [ ] Critical path highlighting
- [ ] Baseline vs actual comparison
- [ ] Export updated schedule back to Excel
- [ ] Role-based access / multi-user logging
