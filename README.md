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

## You choose which tasks show (allowlist)

The dashboard shows **only the tasks you opt into** — so other apps' noise (Zoom,
Nvidia, …) stays out. The **Show** dropdown has three scopes:

- **Pinned only** (default) — just your allowlisted tasks.
- **All my tasks** — every non-Microsoft task, for discovery. Click the **☆** star
  on any row to pin it (★) into your list; click again to unpin.
- **Everything** — includes Windows' own `\Microsoft\...` tasks.

The allowlist lives in **`watch.json`**:

```json
{
  "include": ["mfmonitor-*", "MyBackupJob"],
  "includeFolders": ["\\MyApps\\"]
}
```

A task appears if its **name matches an `include` glob** (e.g. `mfmonitor-*`) **or**
it lives under a **watched Task Scheduler folder** in `includeFolders`. Pinning in
the UI just edits this file for you.

### Making another app's task show up

Three ways — pick whichever suits the app / session that creates the task:

1. **Pin it in the UI** — switch to *All my tasks*, click the ☆. (No coordination.)
2. **Add its name/glob** to `watch.json` → `include` (e.g. `"my-app-*"`).
3. **Register the task under `\MyApps\`** — the dashboard watches that whole folder,
   so it shows up automatically with zero config. In PowerShell:
   `Register-ScheduledTask -TaskName "my-job" -TaskPath "\MyApps\" ...`

**Telling another Claude session** (copy-paste this):

> When you register the Windows scheduled task, register it under TaskPath
> `\MyApps\` (e.g. `Register-ScheduledTask -TaskName "<name>" -TaskPath "\MyApps\" ...`).
> It will then appear automatically in my task dashboard at
> `C:\Users\srava\win-task-dashboard`. If it must live at the root path instead,
> add its name to `C:\Users\srava\win-task-dashboard\watch.json` under `include`.

## What you get

- Your chosen tasks in one clean table: next run, last run, status.
- **▶ Run** button to trigger any task immediately; **Enable/Disable** toggle.
- **☆ / ★** pin toggle to manage your allowlist.
- **Export all** → dumps your (non-Microsoft) tasks to `exports/<timestamp>/*.xml`.
- **Import…** → registers every `*.xml` in a folder (for a new machine).
- Bound to `127.0.0.1` only — triggering tasks is privileged, so it never listens
  on the network.

## JSON API (usable by other apps)

```
GET  /api/tasks?scope=watch           # allowlisted only (default)
GET  /api/tasks?scope=user            #   all non-Microsoft tasks
GET  /api/tasks?scope=system          #   everything, incl. Windows tasks
POST /api/tasks/run      {name, path} # trigger now
POST /api/tasks/enable   {name, path}
POST /api/tasks/disable  {name, path}
POST /api/watch/add      {name}       # pin a task to the allowlist
POST /api/watch/remove   {name}       # unpin
POST /api/export                      # -> {ok, count, folder}
POST /api/import         {folder}     # -> {ok, count}
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
