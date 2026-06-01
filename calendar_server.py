"""
Calendar Web Server — local calendar with .ics import.
View, manage, and import calendar events right from your browser.
Runs alongside the existing AI-powered screenshot → TODO pipeline.
"""

import os
import re
import json
import sys
import tempfile
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=None)

DATA_FILE = Path(__file__).parent / "calendar_data.json"
PENDING_FILE = Path(__file__).parent / "pending_todos.json"
ICS_WATCH_DIR = Path(tempfile.gettempdir())  # where AI-generated .ics files land


# ──────────────────────────────────────────────────────────
# Data layer — JSON file persistence
# ──────────────────────────────────────────────────────────

def load_events():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def save_events(events):
    DATA_FILE.write_text(json.dumps(events, ensure_ascii=False, indent=2), "utf-8")


# ──────────────────────────────────────────────────────────
# ICS parser
# ──────────────────────────────────────────────────────────

def parse_ics(content):
    """Parse .ics content and return list of event dicts."""
    events = []
    current = {}
    in_vevent = False

    for line in content.splitlines():
        line = line.rstrip()
        if line == "BEGIN:VEVENT":
            in_vevent = True
            current = {"id": _uid()}
            continue
        if line == "END:VEVENT":
            if current:
                events.append(current)
            current = {}
            in_vevent = False
            continue
        if not in_vevent:
            continue

        # Handle folded lines (RFC 5545 — a line starting with space/tab is continuation)
        for prefix, key in [("DTSTART;VALUE=DATE:", "date"), ("DTEND;VALUE=DATE:", "end_date"),
                            ("SUMMARY:", "title"), ("DESCRIPTION:", "description")]:
            if prefix in line:
                val = line.split(prefix, 1)[1].strip()
                if key in ("date", "end_date"):
                    try:
                        val = f"{val[:4]}-{val[4:6]}-{val[6:8]}"
                    except (IndexError, ValueError):
                        pass
                current[key] = val
                break

    return events


def _uid():
    import uuid
    return uuid.uuid4().hex[:12]


def import_pending_todos():
    """Load todos from pending_todos.json (fallback when push happens offline)."""
    if not PENDING_FILE.exists():
        return 0
    try:
        pending = json.loads(PENDING_FILE.read_text("utf-8"))
        if not pending:
            return 0
        events = load_events()
        existing = {(e["date"], e["title"]) for e in events}
        priority_colors = {"紧急": "#e74c3c", "重要": "#f39c12", "普通": "#4a90d9"}
        imported = 0
        for item in pending:
            full_title = item.get('title', '未命名')
            key = (item.get("date"), full_title)
            if key in existing:
                continue
            events.append({
                "id": _uid(),
                "title": full_title,
                "date": item.get("date", date.today().isoformat()),
                "description": item.get('source', 'AI提取'),
                "color": "#5b6abf",
            })
            existing.add(key)
            imported += 1
        save_events(events)
        PENDING_FILE.unlink()  # Clear pending file after successful import
        return imported
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────
# API routes
# ──────────────────────────────────────────────────────────

@app.route("/api/events", methods=["GET"])
def api_events():
    month = request.args.get("month")
    if month:
        return jsonify([e for e in load_events() if e.get("date", "").startswith(month)])
    return jsonify(load_events())


@app.route("/api/events", methods=["POST"])
def api_create_event():
    data = request.get_json()
    events = load_events()

    event = {
        "id": _uid(),
        "title": data.get("title", "未命名事件"),
        "date": data.get("date", date.today().isoformat()),
        "description": data.get("description", ""),
        "color": data.get("color", "#4a90d9"),
    }
    events.append(event)
    save_events(events)
    return jsonify(event), 201


@app.route("/api/events/batch", methods=["POST"])
def api_batch_create():
    """Batch-import todos from AI extraction. Deduplicates by date+title."""
    data = request.get_json()
    items = data if isinstance(data, list) else data.get("todos", [])
    if not items:
        return jsonify({"error": "empty batch"}), 400

    events = load_events()
    existing = {(e["date"], e["title"]) for e in events}
    priority_colors = {"紧急": "#e74c3c", "重要": "#f39c12", "普通": "#4a90d9"}
    created = []

    for item in items:
        full_title = item.get('title', '未命名')
        key = (item.get("date"), full_title)
        if key in existing:
            continue
        ev = {
            "id": _uid(),
            "title": full_title,
            "date": item.get("date", date.today().isoformat()),
            "description": f"来源: {item.get('source', 'AI提取')} | 截止: {item.get('deadline', '未指定')}",
            "color": priority_colors.get(item.get("priority"), "#4a90d9"),
        }
        events.append(ev)
        created.append(ev)
        existing.add(key)

    save_events(events)
    return jsonify({"created": len(created), "events": created}), 201


@app.route("/api/events/<eid>", methods=["PUT"])
def api_update_event(eid):
    data = request.get_json()
    events = load_events()
    for ev in events:
        if ev["id"] == eid:
            ev["title"] = data.get("title", ev["title"])
            ev["date"] = data.get("date", ev["date"])
            ev["description"] = data.get("description", ev.get("description", ""))
            ev["color"] = data.get("color", ev.get("color", "#4a90d9"))
            save_events(events)
            return jsonify(ev)
    return jsonify({"error": "not found"}), 404


@app.route("/api/events/<eid>", methods=["DELETE"])
def api_delete_event(eid):
    events = load_events()
    events = [e for e in events if e["id"] != eid]
    save_events(events)
    return jsonify({"ok": True, "deleted": 1})


@app.route("/api/events/batch", methods=["DELETE"])
def api_delete_batch():
    """Delete specific events by IDs, or all if no IDs given."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", None)
    events = load_events()
    if ids:
        events = [e for e in events if e["id"] not in ids]
        deleted = len([e for e in load_events() if e["id"] in ids])
    else:
        deleted = len(events)
        events = []
    save_events(events)
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/import/ics", methods=["POST"])
def api_import_ics():
    """Import events from uploaded .ics file(s)."""
    imported = []
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files"}), 400

    events = load_events()
    existing_dates = {(e["date"], e["title"]) for e in events}

    for f in files:
        if f.filename.endswith(".ics"):
            content = f.read().decode("utf-8", errors="ignore")
            for parsed in parse_ics(content):
                key = (parsed.get("date"), parsed.get("title"))
                if key not in existing_dates:
                    events.append(parsed)
                    imported.append(parsed)
                    existing_dates.add(key)

    save_events(events)
    return jsonify({"imported": len(imported), "events": imported})


@app.route("/api/pending/import", methods=["POST"])
def api_import_pending():
    """Import todos from pending_todos.json (offline fallback)."""
    count = import_pending_todos()
    return jsonify({"imported": count})


@app.route("/api/scan-temp", methods=["POST"])
def api_scan_temp():
    """Scan temp directory for AI-generated .ics files and auto-import them."""
    imported = []
    events = load_events()
    existing = {(e["date"], e["title"]) for e in events}

    try:
        for p in ICS_WATCH_DIR.glob("AI-TODO-*.ics"):
            content = p.read_text("utf-8", errors="ignore")
            for parsed in parse_ics(content):
                key = (parsed.get("date"), parsed.get("title"))
                if key not in existing:
                    events.append(parsed)
                    imported.append(parsed)
                    existing.add(key)
            # Rename processed file so we don't re-import
            try:
                p.rename(p.with_suffix(".ics.imported"))
            except OSError:
                pass

        save_events(events)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"imported": len(imported), "events": imported})


# ──────────────────────────────────────────────────────────
# Frontend — single HTML page
# ──────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📅 AI TODO 日历</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --primary:#5b6abf;--primary-light:#eef1ff;--danger:#e74c3c;--danger-light:#fde8e8;
  --text:#1a1a2e;--text-secondary:#6b7280;
  --border:#e5e7eb;--bg:#f3f4f6;--surface:#fff;--radius:8px;
}
body{font-family:"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;background:var(--bg);color:var(--text);height:100vh;overflow:hidden}

.hidden-input{display:none}

/* ── Toolbar ── */
.toolbar{display:flex;align-items:center;gap:6px;padding:10px 0}
.toolbar .nav-btn{background:var(--surface);border:1px solid var(--border);padding:6px 10px;border-radius:6px;cursor:pointer;font-size:13px;color:var(--text);transition:all .15s;font-family:inherit}
.toolbar .nav-btn:hover{background:var(--bg)}
.toolbar .view-title{font-size:16px;font-weight:700;min-width:140px;text-align:center}
.view-tabs{display:flex;border:1px solid var(--border);border-radius:6px;overflow:hidden;margin-left:auto}
.view-tab{padding:6px 14px;border:none;background:var(--surface);cursor:pointer;font-size:12px;font-weight:500;color:var(--text-secondary);transition:all .15s;font-family:inherit}
.view-tab:not(:last-child){border-right:1px solid var(--border)}
.view-tab:hover{background:var(--bg)}
.view-tab.active{background:var(--primary);color:#fff}

/* ── Calendar Grid ── */
.month-grid{display:grid;grid-template-columns:repeat(7,1fr);grid-template-rows:auto repeat(6,1fr);background:var(--border);gap:1px;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;flex:1;height:100%}
.month-header{background:var(--primary);color:#fff;padding:10px 6px;text-align:center;font-weight:600;font-size:11px}
.month-cell{background:var(--surface);padding:4px 6px;cursor:pointer;transition:background .1s;display:flex;flex-direction:column;overflow:hidden}
.month-cell:hover{background:#fafaff}
.month-cell.other-month{background:#fafafa}
.month-cell.other-month .date-num{color:#c0c0c0}
.month-cell.today{background:var(--primary-light)}
.month-cell.selected{box-shadow:inset 0 0 0 2px var(--primary)}
.date-num{font-size:12px;font-weight:600;margin-bottom:2px;display:inline-flex;align-items:center;justify-content:center;align-self:flex-end}
.month-cell.today .date-num{background:var(--primary);color:#fff;width:24px;height:24px;border-radius:50%}
.events-stack{display:flex;flex-direction:column;gap:1px;overflow:hidden;flex:1}
.event-chip{font-size:10px;padding:2px 5px;border-radius:3px;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.4;font-weight:500}
.more-link{font-size:10px;color:var(--text-secondary);padding:2px 4px;font-weight:500}

/* ── Week Grid ── */
.time-grid{display:flex;flex-direction:column;background:var(--border);gap:1px;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;flex:1}
.time-grid-header{display:flex;background:var(--surface);gap:1px}
.time-gutter{width:50px;flex-shrink:0}
.time-col-header{flex:1;text-align:center;padding:10px 2px;font-size:11px;font-weight:600;background:var(--primary);color:#fff}
.time-col-header.today{background:#764ba2}
.time-row{display:flex;background:var(--surface);gap:1px}
.time-label{width:50px;flex-shrink:0;font-size:10px;color:var(--text-secondary);text-align:right;padding:1px 6px 0 0}
.time-slot{flex:1;min-height:36px;padding:1px 3px;cursor:pointer}
.time-slot:hover{background:#fafaff}
.time-event{font-size:10px;padding:2px 5px;border-radius:3px;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:1px;font-weight:500}

/* ── Day View ── */
.day-event-card{display:flex;align-items:center;gap:10px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 14px;transition:box-shadow .15s}
.day-event-card:hover{box-shadow:0 2px 8px rgba(0,0,0,.06)}
.day-event-card .color-bar{width:4px;height:36px;border-radius:2px;flex-shrink:0}
.day-event-card .info{flex:1;min-width:0}
.day-event-card .title{font-weight:600;font-size:14px}
.day-event-card .desc{font-size:12px;color:var(--text-secondary);margin-top:1px}

/* ── Sidebar (day events) ── */
.day-panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column}
.day-panel-header{padding:10px 14px;background:#fafafa;border-bottom:1px solid var(--border)}
.day-panel-header h3{font-size:13px;font-weight:600}
.day-panel-body{padding:6px 10px;overflow-y:auto;flex:1}
.day-event-row{display:flex;align-items:center;gap:8px;padding:8px;border-radius:6px;transition:background .1s}
.day-event-row:hover{background:var(--bg)}
.day-event-row .dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.day-event-row .info{flex:1;min-width:0}
.day-event-row .info .t{font-weight:500;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.day-event-row .info .d{font-size:11px;color:var(--text-secondary)}

.btn-sm{padding:4px 8px;font-size:11px;border-radius:4px;cursor:pointer;border:none;font-family:inherit}
.btn-del{background:var(--danger-light);color:var(--danger)}

/* ── Toast ── */
.toast-container{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:2000;display:flex;flex-direction:column;gap:4px;align-items:center}
.toast{background:#1a1a2e;color:#fff;padding:8px 18px;border-radius:16px;font-size:12px;font-weight:500;animation:toastIn .3s;box-shadow:0 4px 16px rgba(0,0,0,.2)}
@keyframes toastIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}

/* ── Empty state ── */
.empty-state{text-align:center;padding:24px 16px;color:var(--text-secondary)}
.empty-state p{font-size:12px}

@media(max-width:768px){
  .month-cell{min-height:60px}
  .event-chip{font-size:9px}
}
</style>
</head>
<body>

<div style="display:flex;padding:12px 12px 12px 0;gap:12px;height:100vh">
  <!-- Left sidebar: selected day events -->
  <div style="width:240px;flex-shrink:0">
    <div class="day-panel" id="dayPanel" style="height:100%;display:flex;flex-direction:column;border-radius:0">
      <div class="day-panel-header" style="display:flex;align-items:center;justify-content:space-between">
        <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:4px">
          <input type="checkbox" id="selectAll" onchange="toggleSelectAll()" style="cursor:pointer"> 全选
        </label>
        <span id="dayPanelTitle" style="font-size:12px;font-weight:600"></span>
        <button class="btn-sm btn-del" id="btnBatchDel" onclick="batchDelete()" style="display:none">删除所选</button>
      </div>
      <div class="day-panel-body" id="dayPanelBody" style="flex:1;overflow-y:auto;max-height:none">
        <div class="empty-state" style="padding:16px"><p style="font-size:12px">点击日历日期查看</p></div>
      </div>
    </div>
  </div>

  <!-- Main calendar area -->
  <div style="flex:1;min-width:0;display:flex;flex-direction:column">
    <div class="toolbar">
      <button class="nav-btn" onclick="navPrev()">◀</button>
      <button class="nav-btn" onclick="navToday()" style="font-weight:600">今天</button>
      <span class="view-title" id="viewTitle"></span>
      <button class="nav-btn" onclick="navNext()">▶</button>
      <div class="view-tabs">
        <button class="view-tab active" id="tabMonth" onclick="setView('month')">月</button>
        <button class="view-tab" id="tabWeek" onclick="setView('week')">周</button>
        <button class="view-tab" id="tabDay" onclick="setView('day')">日</button>
      </div>
    </div>
    <div class="calendar-content" id="calendarRoot" style="flex:1;display:flex;flex-direction:column;padding:0"></div>
  </div>
</div>

<!-- Drop overlay -->
<div class="drop-overlay hidden-input" id="dropOverlay" style="display:none"
     ondragover="return false"
     ondragenter="showDropOverlay();return false"
     ondragleave="hideDropOverlay();return false"
     ondrop="onDrop(event);return false">
  <div class="drop-box">
    <div class="icon">📂</div>
    <p>释放以导入 .ics 文件</p>
  </div>
</div>

<div class="toast-container" id="toastContainer"></div>

<script>
// ═══════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════
let events = [];
let currentDate = new Date();
let currentView = 'month';
let selectedDate = fmtLocalDate(new Date());
let miniDate = new Date();

const COLORS = [
  {color:'#5b6abf',label:'默认'},
  {color:'#e74c3c',label:'紧急'},
  {color:'#2ecc71',label:'完成'},
  {color:'#f39c12',label:'重要'},
  {color:'#9b59b6',label:'学习'},
  {color:'#1abc9c',label:'会议'},
  {color:'#e67e22',label:'提醒'},
  {color:'#3498db',label:'日程'},
];

const DAY_NAMES = ['日','一','二','三','四','五','六'];

// ═══════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════
async function init() {
  await fetchEvents();
  renderAll();
}
init();

async function fetchEvents() {
  try { const res = await fetch('/api/events'); events = await res.json(); }
  catch(e) { events = []; }
}

function toast(msg) {
  const c = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = 'toast'; el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => { el.style.opacity='0'; el.style.transition='opacity .3s'; setTimeout(()=>el.remove(),300); }, 2200);
}

// ═══════════════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════════════
function navPrev() {
  const d = new Date(currentDate);
  if (currentView === 'month') d.setMonth(d.getMonth() - 1);
  else if (currentView === 'week') d.setDate(d.getDate() - 7);
  else d.setDate(d.getDate() - 1);
  currentDate = d; miniDate = new Date(d);
  selectedDate = fmtLocalDate(d);
  renderAll();
}

function navNext() {
  const d = new Date(currentDate);
  if (currentView === 'month') d.setMonth(d.getMonth() + 1);
  else if (currentView === 'week') d.setDate(d.getDate() + 7);
  else d.setDate(d.getDate() + 1);
  currentDate = d; miniDate = new Date(d);
  selectedDate = fmtLocalDate(d);
  renderAll();
}

function navToday() {
  currentDate = new Date(); miniDate = new Date();
  selectedDate = fmtLocalDate(new Date());
  renderAll();
}

function setView(view) {
  currentView = view;
  if (view === 'day') { const [y6,m6,d6] = selectedDate.split('-'); currentDate = new Date(+y6, +m6 - 1, +d6); }
  renderAll();
}

function goToDate(dateStr) {
  selectedDate = dateStr;
  const [gy,gm,gd] = dateStr.split('-');
  currentDate = new Date(+gy, +gm - 1, +gd);
  miniDate = new Date(+gy, +gm - 1, +gd);
  currentView = 'day';
  renderAll();
}

function selectDay(dateStr) {
  selectedDate = dateStr;
  renderAll();
}

// ═══════════════════════════════════════════════════════
// Events helpers
// ═══════════════════════════════════════════════════════
function eventsOnDay(ds) { return events.filter(e => e.date === ds); }
function fmtDate(ds) {
  const [y,m,d] = ds.split('-');
  return `${parseInt(m)}月${parseInt(d)}日`;
}
function fmtDateFull(ds) {
  const [y,m,d] = ds.split('-');
  const w = new Date(y, parseInt(m)-1, parseInt(d)).getDay();
  return `${y}年${parseInt(m)}月${parseInt(d)}日 星期${DAY_NAMES[w]}`;
}
function fmtLocalDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}


// ═══════════════════════════════════════════════════════
// Render all
// ═══════════════════════════════════════════════════════
function renderAll() {
  renderViewTabs();
  renderTitle();
  renderCalendar();
  renderDayPanel();
  renderMiniCal();
  renderUpcoming();
}

function renderViewTabs() {
  ['tabMonth','tabWeek','tabDay'].forEach(id => {
    document.getElementById(id).classList.toggle('active', id === 'tab' + currentView.charAt(0).toUpperCase() + currentView.slice(1));
  });
}

function renderTitle() {
  const d = currentDate;
  const y = d.getFullYear(), m = d.getMonth();
  if (currentView === 'month') {
    document.getElementById('viewTitle').textContent = `${y}年 ${m + 1}月`;
  } else if (currentView === 'week') {
    const start = new Date(d); start.setDate(start.getDate() - start.getDay());
    const end = new Date(start); end.setDate(end.getDate() + 6);
    document.getElementById('viewTitle').textContent =
      `${start.getFullYear()}/${start.getMonth()+1}/${start.getDate()} — ${end.getFullYear()}/${end.getMonth()+1}/${end.getDate()}`;
  } else {
    document.getElementById('viewTitle').textContent = fmtDateFull(fmtLocalDate(d));
  }
}



// ═══════════════════════════════════════════════════════
// Mini Calendar (sidebar)
// ═══════════════════════════════════════════════════════
function renderMiniCal() {
  const y = miniDate.getFullYear(), m = miniDate.getMonth();
  const firstDay = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const daysInPrev = new Date(y, m, 0).getDate();
  const today = fmtLocalDate(new Date());

  let html = '<div class="mini-cal">';
  html += '<div class="mini-cal-header">';
  html += `<button onclick="miniDate.setMonth(miniDate.getMonth()-1);renderAll()">◀</button>`;
  html += `<span class="mini-cal-title" onclick="currentDate=new Date(${y},${m},1);currentView='month';renderAll()">${y}年 ${m+1}月</span>`;
  html += `<button onclick="miniDate.setMonth(miniDate.getMonth()+1);renderAll()">▶</button>`;
  html += '</div>';
  html += '<div class="mini-cal-grid">';
  DAY_NAMES.forEach(d => html += `<div class="mini-cal-day-header">${d}</div>`);

  const totalCells = Math.ceil((firstDay + daysInMonth) / 7) * 7;
  for (let i = 0; i < totalCells; i++) {
    let day, ds, cls = 'mini-cal-day';
    if (i < firstDay) {
      day = daysInPrev - firstDay + i + 1;
      ds = formatDateStr(y, m - 1, day);
      cls += ' other';
    } else if (i - firstDay >= daysInMonth) {
      day = i - firstDay - daysInMonth + 1;
      ds = formatDateStr(y, m + 1, day);
      cls += ' other';
    } else {
      day = i - firstDay + 1;
      ds = formatDateStr(y, m, day);
      if (ds === today) cls += ' today';
      if (ds === selectedDate) cls += ' selected';
      if (eventsOnDay(ds).length > 0) cls += ' has-event';
    }
    html += `<div class="${cls}" onclick="selectDay('${ds}');(function(){const p=ds.split('-');currentDate=new Date(+p[0],+p[1]-1,+p[2]);miniDate=new Date(+p[0],+p[1]-1,+p[2])})();renderAll()">${day}</div>`;
  }
  html += '</div></div>';
  document.getElementById('miniCal').innerHTML = html;
}

function formatDateStr(y, m, d) {
  // Use Date constructor to properly handle month rollover
  const dt = new Date(y, m, d);
  return `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`;
}

// ═══════════════════════════════════════════════════════
// Upcoming events (sidebar)
// ═══════════════════════════════════════════════════════
function renderUpcoming() {
  const today = fmtLocalDate(new Date());
  const upcoming = events
    .filter(e => e.date >= today)
    .sort((a,b) => a.date.localeCompare(b.date))
    .slice(0, 8);

  let html = '<ul class="upcoming-list">';
  if (upcoming.length === 0) {
    html += '<div class="upcoming-empty">暂无近期事项<br>截图后将自动导入</div>';
  } else {
    upcoming.forEach(e => {
      const isToday = e.date === today;
      html += `<li class="upcoming-item" onclick="goToDate('${e.date}')" title="${e.title}">`;
      html += `<div class="color-bar" style="background:${e.color||'#5b6abf'}"></div>`;
      html += `<div class="info"><div class="t">${e.title}</div><div class="d">${isToday ? '今天' : fmtDate(e.date)}</div></div>`;
      html += '</li>';
    });
  }
  html += '</ul>';
  document.getElementById('upcomingList').innerHTML = html;

  // Legend
  document.getElementById('legend').innerHTML = COLORS.map(c =>
    `<div class="legend-item" style="cursor:default"><span class="legend-dot" style="background:${c.color}"></span>${c.label}</div>`
  ).join('');
}

// ═══════════════════════════════════════════════════════
// Month View
// ═══════════════════════════════════════════════════════
function renderMonth() {
  const y = currentDate.getFullYear(), m = currentDate.getMonth();
  const firstDay = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const daysInPrev = new Date(y, m, 0).getDate();
  const today = fmtLocalDate(new Date());

  let html = '<div class="month-grid">';
  DAY_NAMES.forEach(d => html += `<div class="month-header">${d}</div>`);

  const totalCells = Math.ceil((firstDay + daysInMonth) / 7) * 7;
  for (let i = 0; i < totalCells; i++) {
    let day, ds, cls = 'month-cell';
    if (i < firstDay) {
      day = daysInPrev - firstDay + i + 1;
      ds = formatDateStr(y, m - 1, day);
      cls += ' other-month';
    } else if (i - firstDay >= daysInMonth) {
      day = i - firstDay - daysInMonth + 1;
      ds = formatDateStr(y, m + 1, day);
      cls += ' other-month';
    } else {
      day = i - firstDay + 1;
      ds = formatDateStr(y, m, day);
      if (ds === today) cls += ' today';
      if (ds === selectedDate) cls += ' selected';
    }

    const dayEvents = eventsOnDay(ds);
    const maxShow = dayEvents.length > 4 ? 3 : 4;

    html += `<div class="${cls}" data-date="${ds}" onclick="selectDay('${ds}')">`;
    html += `<div class="date-num">${day}</div>`;
    html += '<div class="events-stack">';
    dayEvents.slice(0, maxShow).forEach(e => {
      html += `<div class="event-chip" style="background:${e.color||'#5b6abf'}" style="cursor:default" title="${e.title}">${e.title}</div>`;
    });
    const remaining = dayEvents.length - maxShow;
    if (remaining > 0) {
      html += `<div class="more-link" onclick="event.stopPropagation();goToDate('${ds}')">+${remaining} 项更多</div>`;
    }
    html += '</div></div>';
  }

  html += '</div>';
  document.getElementById('calendarRoot').innerHTML = html;
}

// ═══════════════════════════════════════════════════════
// Week View (Google Calendar style)
// ═══════════════════════════════════════════════════════
function renderWeek() {
  const start = new Date(currentDate);
  start.setDate(start.getDate() - start.getDay());
  start.setHours(0,0,0,0);
  const today = fmtLocalDate(new Date());

  let html = '<div class="time-grid">';

  // Header
  html += '<div class="time-grid-header">';
  html += '<div class="time-gutter"></div>';
  for (let i = 0; i < 7; i++) {
    const d = new Date(start); d.setDate(d.getDate() + i);
    const ds = fmtLocalDate(d);
    const isToday = ds === today;
    html += `<div class="time-col-header${isToday ? ' today' : ''}${ds === selectedDate ? ' selected' : ''}">`;
    html += `<div style="font-size:10px;opacity:.8">${DAY_NAMES[i]}</div>`;
    html += `<div style="font-size:15px">${d.getDate()}</div>`;
    html += '</div>';
  }
  html += '</div>';

  // Hour rows
  for (let h = 6; h <= 22; h++) {
    html += '<div class="time-row">';
    html += `<div class="time-label">${String(h).padStart(2,'0')}:00</div>`;
    for (let i = 0; i < 7; i++) {
      const d = new Date(start); d.setDate(d.getDate() + i);
      const ds = fmtLocalDate(d);
      html += `<div class="time-slot" data-date="${ds}" onclick="selectDay('${ds}')">`;
      if (h === 8) { // Show events in the 8am slot as proxies for all-day events
        const dayEvents = eventsOnDay(ds);
        dayEvents.forEach(e => {
          html += `<div class="time-event" style="background:${e.color||'#5b6abf'}" style="cursor:default">${e.title}</div>`;
        });
      }
      html += '</div>';
    }
    html += '</div>';
  }

  html += '</div>';
  document.getElementById('calendarRoot').innerHTML = html;
}

// ═══════════════════════════════════════════════════════
// Day View
// ═══════════════════════════════════════════════════════
function renderDay() {
  const ds = fmtLocalDate(currentDate);
  const today = fmtLocalDate(new Date());
  const dayEvents = eventsOnDay(ds);

  if (dayEvents.length === 0) {
    document.getElementById('calendarRoot').innerHTML = `
      <div class="empty-state">
        <div class="icon">📭</div>
        <p><strong>${ds === today ? '今天' : fmtDate(ds)} 暂无安排</strong></p>
        <p style="margin-top:8px">截图后 AI 将自动导入事项</p>
      </div>
    `;
    return;
  }

  let html = '<div style="display:flex;flex-direction:column;gap:6px">';
  dayEvents.forEach(e => {
    html += `
      <div class="day-event-card">
        <div class="color-bar" style="background:${e.color||'#5b6abf'}"></div>
        <div class="info">
          <div class="title">${e.title}</div>
          ${e.description ? `<div class="desc">${e.description}</div>` : ''}
        </div>
        <button class="btn-sm btn-del" onclick="event.stopPropagation();deleteEvent('${e.id}')">删除</button>
      </div>`;
  });
  html += '</div>';
  document.getElementById('calendarRoot').innerHTML = html;
}

// ═══════════════════════════════════════════════════════
// Day detail panel
// ═══════════════════════════════════════════════════════
function renderDayPanel() {
  const panel = document.getElementById('dayPanel');
  if (!selectedDate) { panel.parentElement.style.display = 'none'; return; }
  panel.parentElement.style.display = '';

  const dayEvents = eventsOnDay(selectedDate);
  document.getElementById('dayPanelTitle').textContent = `${fmtDate(selectedDate)} · ${dayEvents.length}项`;

  const body = document.getElementById('dayPanelBody');
  if (dayEvents.length === 0) {
    body.innerHTML = '<div class="empty-state" style="padding:24px"><p>当天没有安排</p></div>';
    return;
  }
  body.innerHTML = dayEvents.map(e => {
    const p = PRIORITIES.find(p => p.color === (e.color||'#5b6abf')) || PRIORITIES[0];
    return `<div class="day-event-row">
      <input type="checkbox" class="event-check" data-id="${e.id}" onchange="updateBatchBtn()" style="cursor:pointer;flex-shrink:0">
      <span class="dot" style="background:${e.color||'#5b6abf'};cursor:pointer" onclick="cycleColor('${e.id}', this)" title="点击切换: ${p.label}"></span>
      <div class="info">
        <div class="t">${e.title}<span class="priority-label" style="font-size:10px;color:${e.color||'#5b6abf'};margin-left:6px">${p.label}</span></div>
        ${e.description ? `<div class="d">${e.description}</div>` : ''}
      </div>
    </div>`;
  }).join('');
  document.getElementById('selectAll').checked = false;
  updateBatchBtn();
}

// ═══════════════════════════════════════════════════════
// Calendar router
// ═══════════════════════════════════════════════════════
function renderCalendar() {
  if (currentView === 'month') renderMonth();
  else if (currentView === 'week') renderWeek();
  else renderDay();
}

// ═══════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════
function toggleSelectAll() {
  const all = document.getElementById('selectAll').checked;
  document.querySelectorAll('.event-check').forEach(cb => { cb.checked = all; });
  updateBatchBtn();
}

function updateBatchBtn() {
  const checked = document.querySelectorAll('.event-check:checked').length;
  document.getElementById('btnBatchDel').style.display = checked > 0 ? '' : 'none';
  document.getElementById('btnBatchDel').textContent = `删除所选(${checked})`;
}

async function batchDelete() {
  const ids = [...document.querySelectorAll('.event-check:checked')].map(cb => cb.dataset.id);
  if (ids.length === 0) return;
  if (!confirm(`确认删除选中的 ${ids.length} 条事项？`)) return;
  await fetch('/api/events/batch', {
    method: 'DELETE',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ids})
  });
  toast(`已删除 ${ids.length} 条`);
  document.getElementById('selectAll').checked = false;
  await fetchEvents();
  renderAll();
}

const PRIORITIES = [
  {color:'#5b6abf', label:'普通'},
  {color:'#f39c12', label:'重要'},
  {color:'#e74c3c', label:'紧急'},
];

async function cycleColor(eid, dot) {
  const ev = events.find(e => e.id === eid);
  if (!ev) return;
  const cur = ev.color || '#5b6abf';
  const idx = PRIORITIES.findIndex(p => p.color === cur);
  const next = PRIORITIES[(idx + 1) % PRIORITIES.length];
  ev.color = next.color;
  dot.style.background = next.color;
  dot.title = next.label;
  // Update sidebar label
  const label = dot.nextElementSibling?.nextElementSibling?.querySelector('.priority-label');
  if (label) label.textContent = next.label;
  await fetch(`/api/events/${eid}`, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({color: next.color, title: ev.title, date: ev.date, description: ev.description || ''})
  });
  renderCalendar();
}

async function deleteEvent(eid) {
  if (!eid) return;
  if (!confirm('确认删除此事件？')) return;
  try {
    await fetch(`/api/events/${eid}`, { method: 'DELETE' });
    toast('🗑 事件已删除');
    await fetchEvents();
    renderAll();
  } catch(e) {
    toast('❌ 删除失败: ' + e.message);
  }
}

// ═══════════════════════════════════════════════════════
// ICS Import
// ═══════════════════════════════════════════════════════
function showDropOverlay() { document.getElementById('dropOverlay').classList.add('show'); }
function hideDropOverlay() { document.getElementById('dropOverlay').classList.remove('show'); }


function onDrop(e) {
  e.preventDefault();
  const files = e.dataTransfer.files;
  if (files.length > 0) importIcs(files);
}

async function importIcs(fileList) {
  const form = new FormData();
  let count = 0;
  for (const f of fileList) {
    if (f.name.toLowerCase().endsWith('.ics')) { form.append('files', f); count++; }
  }
  if (count === 0) { toast('⚠️ 请选择 .ics 文件'); return; }

  try {
    const res = await fetch('/api/import/ics', { method: 'POST', body: form });
    const data = await res.json();
    if (data.imported > 0) {
      toast(`✅ 成功导入 ${data.imported} 条事件`);
    } else {
      toast('未发现新事件（可能已存在）');
    }
    document.getElementById('icsInput').value = '';
    await fetchEvents();
    renderAll();
  } catch(e) {
    toast('❌ 导入失败: ' + e.message);
  }
}

async function scanTempDir() {
  toast('🔍 正在扫描 AI 生成的待办事项...');
  try {
    const res = await fetch('/api/scan-temp', { method: 'POST' });
    const data = await res.json();
    if (data.error) { toast('⚠️ ' + data.error); return; }
    if (data.imported > 0) {
      toast(`✅ 从 AI 导入了 ${data.imported} 条待办事项`);
    } else {
      toast('ℹ️ 未发现新的 AI 待办事项');
    }
    await fetchEvents();
    renderAll();
  } catch(e) {
    toast('❌ 扫描失败，请确认日历服务器正在运行');
  }
}

// ═══════════════════════════════════════════════════════
// Keyboard shortcuts
// ═══════════════════════════════════════════════════════
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  switch(e.key) {
    case 'ArrowLeft': e.preventDefault(); navPrev(); break;
    case 'ArrowRight': e.preventDefault(); navNext(); break;
    case 't': navToday(); break;
    case 'm': setView('month'); break;
    case 'w': setView('week'); break;
    case 'd': setView('day'); break;
  }
});


// ═══════════════════════════════════════════════════════
// Auto-poll: check for new events every 2 seconds
// ═══════════════════════════════════════════════════════
let lastEventHash = JSON.stringify(events);
setInterval(async () => {
  try {
    // Check for pending todos from offline push
    await fetch('/api/pending/import', { method: 'POST' });
    const res = await fetch('/api/events');
    const latest = await res.json();
    const hash = JSON.stringify(latest);
    if (hash !== lastEventHash) {
      const prevCount = events.length;
      events = latest;
      lastEventHash = hash;
      if (latest.length !== prevCount) {
        const added = latest.length - prevCount;
        if (added > 0) {
          const badge = document.createElement('div');
          badge.style.cssText = 'position:fixed;top:72px;right:24px;background:#e74c3c;color:#fff;padding:12px 22px;border-radius:8px;font-weight:700;font-size:14px;z-index:3000;animation:toastIn .3s;box-shadow:0 4px 20px rgba(231,76,60,.4)';
          badge.textContent = `🆕 AI 导入 ${added} 条新事项`;
          document.body.appendChild(badge);
          setTimeout(() => { badge.style.opacity='0'; badge.style.transition='opacity .4s'; setTimeout(() => badge.remove(), 400); }, 2800);
        }
        renderAll();
      }
    }
  } catch(e) { /* server not running yet — ignore */ }
}, 2000);

// Update status dot with real server health check
setInterval(async () => {
  const dot = document.getElementById('statusDot');
  if (!dot) return;
  try {
    const res = await fetch('/api/events');
    dot.style.background = res.ok ? '#2ecc71' : '#e74c3c';
  } catch(e) {
    dot.style.background = '#e74c3c';
  }
}, 5000);
</script>

</body>
</html>"""


@app.route("/")
def index():
    return HTML


if __name__ == "__main__":
    # Import any pending todos that were saved while server was offline
    imported = import_pending_todos()
    if imported:
        print(f"📥 已导入 {imported} 条暂存的待办事项（离线时保存）")

    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    print(f"\n📅 AI TODO 日历已启动 → http://{host}:{port}\n")

    try:
        from waitress import serve
        serve(app, host=host, port=port)
    except ImportError:
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        app.run(host=host, port=port, debug=False)
