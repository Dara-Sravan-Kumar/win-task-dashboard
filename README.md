# win-task-dashboard

A tiny, zero-dependency local web dashboard **and JSON API** for the Windows Task
Scheduler. See every scheduled task, when it runs next, and **trigger any of them
with one click** — plus export/import for moving your tasks to a new machine.

Standard-library Python only. No `pip install`.

## Run

```powershell
python app.py                 # opens http://127.0.0.1:8787 in your browser
python app.py --port 9000     # custom port
python app.py --no-browser    # don't auto-open a browser (e.g. when run as a service)
```

or double-click / run `run.ps1`.

## What you get

- **All tasks** in one clean table: next run, last run, status. Windows' own
  `\Microsoft\...` tasks are hidden by default — tick **show Microsoft tasks** to see them.
- **▶ Run** button to trigger any task immediately; **Enable/Disable** toggle.
- **Export all** → dumps your (non-Microsoft) tasks to `exports/<timestamp>/*.xml`.
- **Import…** → registers every `*.xml` in a folder (for a new machine).
- Bound to `127.0.0.1` only — triggering tasks is privileged, so it never listens
  on the network.

## JSON API (usable by other apps)

```
GET  /api/tasks?all=0                 # list tasks (all=1 includes Microsoft tasks)
POST /api/tasks/run     {name, path}  # trigger now
POST /api/tasks/enable  {name, path}
POST /api/tasks/disable {name, path}
POST /api/export                      # -> {ok, count, folder}
POST /api/import        {folder}      # -> {ok, count}
```

Example:

```powershell
# list, as JSON
irm http://127.0.0.1:8787/api/tasks | % tasks | ft name,next,state

# trigger a task
irm http://127.0.0.1:8787/api/tasks/run -Method Post -ContentType application/json `
    -Body '{"name":"mfmonitor-report","path":"\\"}'
```

## Migrating to a new computer

1. On the **old** PC: click **Export all** (or `POST /api/export`). Copy the
   produced `exports/<timestamp>` folder to the new machine (USB, OneDrive, git…).
2. On the **new** PC: run the dashboard, click **Import…**, and point it at that
   folder. Each task is re-registered under your current user account.

Caveats the import handles / you should know:
- The task's **user account** is rewritten to the new machine's current user.
- A task's **action still has to exist** on the new PC. E.g. a task that runs
  `C:\...\project\.venv\Scripts\app.exe` needs that path recreated there. For
  app-specific tasks it's often cleaner to re-run that app's own register script
  (which recomputes paths) than to import its XML.
- Imported tasks land at the root `\` path (folder structure isn't preserved).

## Optional: always-on service

To keep the API available in the background and start it at logon, run
`register_autostart.ps1` (registers a hidden `python app.py --no-browser` task).
Remove it with `Unregister-ScheduledTask -TaskName win-task-dashboard`.

## Requirements

Windows + Python 3.8+. Uses the built-in `ScheduledTasks` PowerShell module.
Reading tasks and running your own tasks needs no admin; modifying some system
tasks may require an elevated session.
