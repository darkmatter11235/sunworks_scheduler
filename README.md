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
| CSV / Excel import | MS-Project style exports (WBS, Duration, Start, Finish, Predecessors, % Complete) |
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

---

## Importing a schedule

1. Click **➕ New project** in the sidebar and give it a name.
2. Click **📥 Import schedule** and upload your CSV or Excel file.

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
tasks           id, project_id, row_num, wbs, task_name, duration_days,
                start_date, finish_date, predecessors, pct_complete,
                wbs_level, is_summary, notes, updated_at
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
