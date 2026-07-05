"""win-task-dashboard - a zero-dependency local web UI + API for Windows Task Scheduler.

Lists every scheduled task (next/last run, status), triggers them on demand,
enables/disables them, and exports/imports them for migrating to a new machine.
Standard library only - no pip installs.

    python app.py                 # opens http://127.0.0.1:8787
    python app.py --port 9000     # custom port
    python app.py --no-browser    # don't auto-open a browser (e.g. as a service)

HTTP/JSON API (also usable by other applications):
    GET  /api/tasks?all=0                 list tasks (all=1 includes Microsoft tasks)
    POST /api/tasks/run     {name, path}  trigger a task now
    POST /api/tasks/enable  {name, path}
    POST /api/tasks/disable {name, path}
    POST /api/export                      dump non-Microsoft tasks to exports/<ts>/*.xml
    POST /api/import        {folder}      register every *.xml in a folder

Binds to 127.0.0.1 only (triggering tasks is privileged; keep it local).
"""

from __future__ import annotations

import fnmatch
import http.server
import json
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 8787
ROOT = Path(__file__).resolve().parent
EXPORT_ROOT = ROOT / "exports"

# The dashboard shows ONLY tasks you opt into (an allowlist), so other apps'
# noise (Zoom, Nvidia, ...) stays out. Membership is either an explicit
# name/glob in "include", or living under a watched Task Scheduler folder.
WATCH_FILE = ROOT / "watch.json"
_DEFAULT_WATCH = {"include": ["mfmonitor-*"], "includeFolders": ["\\MyApps\\"]}


def load_watch() -> dict:
    if not WATCH_FILE.exists():
        WATCH_FILE.write_text(json.dumps(_DEFAULT_WATCH, indent=2), encoding="utf-8")
        return dict(_DEFAULT_WATCH)
    try:
        data = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"include": [], "includeFolders": []}
    data.setdefault("include", [])
    data.setdefault("includeFolders", [])
    return data


def save_watch(watch: dict) -> None:
    WATCH_FILE.write_text(json.dumps(watch, indent=2), encoding="utf-8")


def is_watched(task: dict, watch: dict) -> bool:
    name = task.get("name", "")
    path = task.get("path", "")
    if any(fnmatch.fnmatch(name, pat) for pat in watch.get("include", [])):
        return True
    return any(path.startswith(f) for f in watch.get("includeFolders", []))

_PREP = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "


def _ps(command: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PREP + command],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def _q(value: str) -> str:
    """Single-quote a value for safe embedding in a PowerShell command."""
    return "'" + str(value).replace("'", "''") + "'"


def _action(inner: str) -> tuple[bool, str]:
    rc, _out, err = _ps("try { %s } catch { Write-Error $_.Exception.Message; exit 1 }" % inner)
    return rc == 0, err.strip()


_LIST_CMD = r"""
Get-ScheduledTask | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
  [pscustomobject]@{
    name   = $_.TaskName
    path   = $_.TaskPath
    state  = [string]$_.State
    author = $_.Author
    action = (($_.Actions | ForEach-Object { $_.Execute }) -join '; ')
    next   = $(if ($i -and $i.NextRunTime) { $i.NextRunTime.ToString('o') } else { $null })
    last   = $(if ($i -and $i.LastRunTime) { $i.LastRunTime.ToString('o') } else { $null })
    result = $(if ($i) { $i.LastTaskResult } else { $null })
  }
} | ConvertTo-Json -Depth 3
"""


def list_tasks(scope: str = "watch") -> list[dict]:
    """scope: 'watch' (allowlisted only, default), 'user' (all non-Microsoft,
    for discovery), or 'system' (everything, incl. Windows tasks)."""
    rc, out, err = _ps(_LIST_CMD)
    if not out.strip():
        raise RuntimeError(err.strip() or "Get-ScheduledTask returned nothing")
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]

    watch = load_watch()
    for t in data:
        t["watched"] = is_watched(t, watch)

    if scope == "watch":
        data = [t for t in data if t["watched"]]
    elif scope == "user":
        data = [t for t in data if not str(t.get("path", "")).startswith("\\Microsoft\\")]
    # scope == "system": keep everything

    data.sort(key=lambda t: (t.get("next") or "9999", (t.get("name") or "").lower()))
    return data


def run_task(name: str, path: str) -> tuple[bool, str]:
    return _action("Start-ScheduledTask -TaskName %s -TaskPath %s -ErrorAction Stop"
                   % (_q(name), _q(path)))


def set_enabled(name: str, path: str, enabled: bool) -> tuple[bool, str]:
    verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
    return _action("%s -TaskName %s -TaskPath %s -ErrorAction Stop | Out-Null"
                   % (verb, _q(name), _q(path)))


def export_all() -> tuple[bool, str, str]:
    dest = EXPORT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    cmd = (
        "$dest = %s; New-Item -ItemType Directory -Force -Path $dest | Out-Null; " % _q(str(dest))
        + "Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } | ForEach-Object { "
        + "  $xml = Export-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath; "
        + "  $safe = ($_.TaskName -replace '[\\\\/:*?\"<>|]','_'); "
        + "  $xml | Out-File -FilePath (Join-Path $dest ($safe + '.xml')) -Encoding utf8 "
        + "}; (Get-ChildItem $dest -Filter *.xml).Count"
    )
    rc, out, err = _ps("try { %s } catch { Write-Error $_.Exception.Message; exit 1 }" % cmd)
    return rc == 0, out.strip(), (err.strip() or str(dest))


def watch_add(name: str) -> None:
    watch = load_watch()
    if name not in watch["include"]:
        watch["include"].append(name)
        save_watch(watch)


def watch_remove(name: str) -> None:
    """Remove a task from the allowlist — drops an exact 'include' entry and
    any glob that matched it (so pinning off always works)."""
    watch = load_watch()
    watch["include"] = [p for p in watch["include"]
                        if p != name and not fnmatch.fnmatch(name, p)]
    save_watch(watch)


def import_all(folder: str) -> tuple[bool, str, str]:
    cmd = (
        "$user = \"$env:USERDOMAIN\\$env:USERNAME\"; $n = 0; "
        + "Get-ChildItem -Path %s -Filter *.xml | ForEach-Object { " % _q(folder)
        + "  $xml = Get-Content $_.FullName -Raw; "
        + "  $name = [System.IO.Path]::GetFileNameWithoutExtension($_.Name); "
        + "  try { Register-ScheduledTask -Xml $xml -TaskName $name -User $user -Force | Out-Null; $n++ } "
        + "  catch { Write-Warning $_.Exception.Message } "
        + "}; $n"
    )
    rc, out, err = _ps("try { %s } catch { Write-Error $_.Exception.Message; exit 1 }" % cmd)
    return rc == 0, out.strip(), err.strip()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):  # keep the console quiet
        pass

    def _send(self, code: int, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            self._send(200, HTML, "text/html")
        elif route.path == "/api/tasks":
            scope = parse_qs(route.query).get("scope", ["watch"])[0]
            if scope not in ("watch", "user", "system"):
                scope = "watch"
            try:
                self._send(200, json.dumps({"scope": scope, "tasks": list_tasks(scope)}))
            except Exception as exc:  # noqa: BLE001
                self._send(500, json.dumps({"error": str(exc)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        b = self._body()
        try:
            if self.path == "/api/tasks/run":
                ok, err = run_task(b["name"], b.get("path", "\\"))
                self._send(200 if ok else 500, json.dumps({"ok": ok, "error": err}))
            elif self.path in ("/api/tasks/enable", "/api/tasks/disable"):
                ok, err = set_enabled(b["name"], b.get("path", "\\"),
                                      self.path.endswith("enable"))
                self._send(200 if ok else 500, json.dumps({"ok": ok, "error": err}))
            elif self.path == "/api/export":
                ok, count, info = export_all()
                self._send(200 if ok else 500,
                           json.dumps({"ok": ok, "count": count, "folder": info}))
            elif self.path == "/api/import":
                ok, count, err = import_all(b["folder"])
                self._send(200 if ok else 500,
                           json.dumps({"ok": ok, "count": count, "error": err}))
            elif self.path == "/api/watch/add":
                watch_add(b["name"])
                self._send(200, json.dumps({"ok": True}))
            elif self.path == "/api/watch/remove":
                watch_remove(b["name"])
                self._send(200, json.dumps({"ok": True}))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except KeyError as exc:
            self._send(400, json.dumps({"error": "missing field %s" % exc}))
        except Exception as exc:  # noqa: BLE001
            self._send(500, json.dumps({"error": str(exc)}))


def main() -> None:
    global PORT
    argv = sys.argv[1:]
    if "--port" in argv:
        PORT = int(argv[argv.index("--port") + 1])
    open_browser = "--no-browser" not in argv

    EXPORT_ROOT.mkdir(exist_ok=True)
    httpd = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d" % (HOST, PORT)
    print("win-task-dashboard on %s  (Ctrl+C to stop)" % url)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Windows Task Dashboard</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.5 system-ui, "Segoe UI", sans-serif;
         background: Canvas; color: CanvasText; }
  header { display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
           padding: 14px 18px; border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
           position: sticky; top: 0; background: Canvas; }
  h1 { font-size: 16px; margin: 0 auto 0 0; }
  button { font: inherit; padding: 6px 12px; border-radius: 8px; cursor: pointer;
           border: 1px solid color-mix(in srgb, CanvasText 25%, transparent);
           background: color-mix(in srgb, CanvasText 6%, Canvas); color: inherit; }
  button:hover { background: color-mix(in srgb, CanvasText 14%, Canvas); }
  button.run { border-color: #2f9e44; color: #2f9e44; font-weight: 600; }
  button.run:hover { background: #2f9e4422; }
  label.toggle { display: flex; gap: 6px; align-items: center; user-select: none; }
  .wrap { padding: 8px 18px 40px; overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; min-width: 760px; }
  th, td { text-align: left; padding: 9px 12px; vertical-align: top;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 10%, transparent); }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; opacity: .7; }
  tr:hover td { background: color-mix(in srgb, CanvasText 5%, transparent); }
  .name { font-weight: 600; }
  .path, .action { opacity: .6; font-size: 12px; word-break: break-all; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px;
          background: color-mix(in srgb, CanvasText 12%, transparent); }
  .pill.Ready { background: #2f9e4426; color: #2f9e44; }
  .pill.Disabled { background: #e0353526; color: #e03535; }
  .pill.Running { background: #1c7ed626; color: #1c7ed6; }
  .muted { opacity: .55; }
  .actions { display: flex; gap: 6px; white-space: nowrap; }
  select { font: inherit; padding: 5px 8px; border-radius: 8px; color: inherit;
           background: color-mix(in srgb, CanvasText 6%, Canvas);
           border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  button.pin { border: none; background: none; font-size: 18px; padding: 2px 6px;
               line-height: 1; color: color-mix(in srgb, CanvasText 40%, transparent); }
  button.pin.on { color: #f5a623; }
  #msg { padding: 8px 18px; font-size: 13px; min-height: 20px; }
  .err { color: #e03535; } .ok { color: #2f9e44; }
</style>
</head>
<body>
<header>
  <h1>Windows Task Dashboard</h1>
  <label class="toggle">Show:
    <select id="scope">
      <option value="watch">Pinned only</option>
      <option value="user">All my tasks</option>
      <option value="system">Everything (incl. system)</option>
    </select>
  </label>
  <button onclick="load()">Refresh</button>
  <button onclick="doExport()">Export all</button>
  <button onclick="doImport()">Import…</button>
</header>
<div id="msg"></div>
<div class="wrap">
  <table>
    <thead><tr>
      <th></th><th>Task</th><th>Next run</th><th>Last run</th><th>Status</th><th></th>
    </tr></thead>
    <tbody id="rows"><tr><td colspan="6" class="muted">Loading…</td></tr></tbody>
  </table>
</div>
<script>
const $ = s => document.querySelector(s);
function fmt(iso){ if(!iso) return '—';
  const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(); }
function resultText(r){ if(r===0) return 'OK';
  if(r===267011||r===267009) return 'not yet run';
  return r==null ? '' : '0x'+(r>>>0).toString(16); }
function msg(t, cls){ const m=$('#msg'); m.textContent=t; m.className=cls||''; }

let SCOPE = 'watch';
async function load(){
  SCOPE = $('#scope').value;
  try {
    const r = await fetch('/api/tasks?scope='+SCOPE);
    const j = await r.json();
    if(j.error){ msg('Error: '+j.error,'err'); return; }
    render(j.tasks);
    msg(j.tasks.length + (SCOPE==='watch' ? ' pinned task(s)' : ' task(s)'), 'muted');
  } catch(e){ msg('Failed to load: '+e,'err'); }
}
function render(tasks){
  const rows = tasks.map(t => {
    const st = (t.state||'').replace(/[^A-Za-z]/g,'');
    const star = t.watched ? '★' : '☆';
    return `<tr>
      <td><button class="pin ${t.watched?'on':''}" title="Pin to dashboard"
                  onclick='pin(${J(t)}, ${t.watched?1:0}, this)'>${star}</button></td>
      <td><div class="name">${esc(t.name)}</div>
          <div class="path">${esc(t.path)}${t.action?' · '+esc(t.action):''}</div></td>
      <td>${fmt(t.next)}</td>
      <td>${fmt(t.last)} <span class="muted">${resultText(t.result)}</span></td>
      <td><span class="pill ${st}">${esc(t.state)}</span></td>
      <td><div class="actions">
        <button class="run" onclick='act("run",${J(t)},this)'>▶ Run</button>
        ${t.state==='Disabled'
          ? `<button onclick='act("enable",${J(t)},this)'>Enable</button>`
          : `<button onclick='act("disable",${J(t)},this)'>Disable</button>`}
      </div></td></tr>`;
  }).join('');
  const empty = SCOPE==='watch'
    ? 'No pinned tasks yet — switch to <b>All my tasks</b> and click ☆ to add ones you want.'
    : 'No tasks.';
  $('#rows').innerHTML = rows || `<tr><td colspan="6" class="muted">${empty}</td></tr>`;
}
async function pin(t, isOn, btn){
  btn.disabled = true;
  const kind = isOn ? 'remove' : 'add';
  try {
    await fetch('/api/watch/'+kind, {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:t.name})});
    msg((isOn?'Unpinned ':'Pinned ')+t.name, 'ok');
  } catch(e){ msg('Pin failed: '+e,'err'); }
  finally { btn.disabled=false; load(); }
}
const esc = s => (s==null?'':String(s)).replace(/[&<>'"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const J = t => JSON.stringify({name:t.name, path:t.path}).replace(/'/g,'&#39;');

async function act(kind, t, btn){
  btn.disabled = true; msg(kind+' '+t.name+'…');
  try {
    const r = await fetch('/api/tasks/'+kind, {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(t)});
    const j = await r.json();
    if(j.ok){
      msg(kind==='run'
        ? '▶ Started '+t.name+' — running in the background; watch its status/last-run here.'
        : kind+' ✓ '+t.name, 'ok');
    }
    else { msg(kind+' failed: '+(j.error||'unknown'), 'err'); }
  } catch(e){ msg('Request failed: '+e,'err'); }
  finally { btn.disabled = false; setTimeout(load, 600); }
}
async function doExport(){
  msg('Exporting…');
  const r = await fetch('/api/export',{method:'POST'});
  const j = await r.json();
  if(j.ok) msg('Exported '+j.count+' tasks to '+j.folder, 'ok');
  else msg('Export failed: '+(j.folder||''), 'err');
}
async function doImport(){
  const folder = prompt('Folder containing exported *.xml task files:');
  if(!folder) return;
  msg('Importing from '+folder+'…');
  const r = await fetch('/api/import',{method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({folder})});
  const j = await r.json();
  if(j.ok) msg('Imported '+j.count+' tasks.', 'ok');
  else msg('Import failed: '+(j.error||'unknown'), 'err');
  load();
}
$('#scope').addEventListener('change', load);
load();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
