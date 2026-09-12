"""Experimental browser dashboard transport for terminal TritonDFT sessions.

The browser service never runs scientific or cluster commands.  It exposes a
bounded view of a registered workflow directory and exchanges approval
decisions with the authoritative terminal process through a small SQLite
mailbox.  The existing Tk/X11 implementation remains in cluster_agent.py.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import io
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field


LOG = logging.getLogger("tritondft.browser_dashboard")
DASHBOARD_API_VERSION = 4
SAFE_TEXT_SUFFIXES = {".in", ".out", ".txt", ".json", ".log", ".sh", ".cif", ".gnu", ".dat", ".xml", ".csv"}
SAFE_IMAGE_SUFFIXES = {".png"}
SAFE_VASP_NAMES = {"POSCAR", "INCAR", "KPOINTS"}
SKIP_PARTS = {".save", "pseudos"}


def _now() -> float:
    return time.time()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class DashboardStore:
    """Process-safe session metadata and command mailbox."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS dashboard_sessions (
                    session_id TEXT PRIMARY KEY,
                    token_hash TEXT UNIQUE NOT NULL,
                    owner TEXT NOT NULL,
                    run_dir TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_activity REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    closed INTEGER NOT NULL DEFAULT 0,
                    approval_json TEXT,
                    decision_json TEXT,
                    message_json TEXT,
                    event_version INTEGER NOT NULL DEFAULT 1
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS dashboard_questions (
                    question_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT,
                    detail TEXT,
                    created_at REAL NOT NULL,
                    completed_at REAL
                )"""
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def create_session(self, run_dir: str | Path, owner: str, ttl_seconds: int) -> tuple[str, str]:
        root = Path(run_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        session_id = secrets.token_hex(12)
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO dashboard_sessions
                   (session_id, token_hash, owner, run_dir, created_at, last_activity, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, _token_hash(token), owner, str(root), now, now, now + ttl_seconds),
            )
        LOG.info("dashboard session created id=%s owner=%s", session_id, owner)
        return session_id, token

    def get(self, token: str, *, touch: bool = True) -> dict[str, Any] | None:
        if not token or len(token) < 32:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM dashboard_sessions WHERE token_hash=? AND closed=0 AND expires_at>?",
                (_token_hash(token), _now()),
            ).fetchone()
            if row is None:
                return None
            if touch:
                db.execute(
                    "UPDATE dashboard_sessions SET last_activity=?, event_version=event_version+1 WHERE session_id=?",
                    (_now(), row["session_id"]),
                )
            return dict(row)

    def publish_approval(self, token: str, payload: dict[str, Any]) -> None:
        session = self.get(token)
        if session is None:
            raise KeyError("session unavailable")
        with self._connect() as db:
            db.execute(
                """UPDATE dashboard_sessions SET approval_json=?, decision_json=NULL,
                   event_version=event_version+1 WHERE session_id=?""",
                (_json(payload), session["session_id"]),
            )

    def submit_decision(self, token: str, decision: dict[str, Any]) -> bool:
        session = self.get(token)
        if session is None:
            return False
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE dashboard_sessions SET decision_json=?, event_version=event_version+1
                   WHERE session_id=? AND approval_json IS NOT NULL AND decision_json IS NULL""",
                (_json(decision), session["session_id"]),
            )
        return cursor.rowcount == 1

    def take_decision(self, token: str) -> dict[str, Any] | None:
        session = self.get(token, touch=False)
        if session is None or not session.get("decision_json"):
            return None
        decision = json.loads(session["decision_json"])
        with self._connect() as db:
            db.execute(
                "UPDATE dashboard_sessions SET approval_json=NULL, decision_json=NULL, event_version=event_version+1 WHERE session_id=?",
                (session["session_id"],),
            )
        return decision

    def post_message(self, token: str, text: str) -> None:
        session = self.get(token)
        if session is None:
            raise KeyError("session unavailable")
        payload = {"text": text[:4000], "created_at": _now()}
        with self._connect() as db:
            db.execute(
                "UPDATE dashboard_sessions SET message_json=?, event_version=event_version+1 WHERE session_id=?",
                (_json(payload), session["session_id"]),
            )
        LOG.info("browser message received id=%s", session["session_id"])

    def take_message(self, token: str) -> dict[str, Any] | None:
        session = self.get(token, touch=False)
        if session is None or not session.get("message_json"):
            return None
        value = json.loads(session["message_json"])
        with self._connect() as db:
            db.execute(
                "UPDATE dashboard_sessions SET message_json=NULL, event_version=event_version+1 WHERE session_id=?",
                (session["session_id"],),
            )
        return value

    def close(self, token: str) -> bool:
        session = self.get(token, touch=False)
        if session is None:
            return False
        with self._connect() as db:
            db.execute(
                "UPDATE dashboard_sessions SET closed=1, event_version=event_version+1 WHERE session_id=?",
                (session["session_id"],),
            )
        LOG.info("dashboard session closed id=%s", session["session_id"])
        return True

    def cleanup(self) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE dashboard_sessions SET closed=1 WHERE closed=0 AND expires_at<=?",
                (_now(),),
            )
        if cursor.rowcount:
            LOG.info("dashboard sessions expired count=%s", cursor.rowcount)
        return cursor.rowcount

    def create_question(self, token: str, question: str) -> str:
        session = self.get(token)
        if session is None:
            raise KeyError("session unavailable")
        question_id = secrets.token_hex(12)
        with self._connect() as db:
            db.execute(
                "INSERT INTO dashboard_questions VALUES (?, ?, 'working', ?, NULL, NULL, ?, NULL)",
                (question_id, session["session_id"], question, _now()),
            )
        return question_id

    def complete_question(self, question_id: str, answer: str, detail: str, status: str = "done") -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE dashboard_questions SET status=?, answer=?, detail=?, completed_at=? WHERE question_id=?",
                (status, answer, detail, _now(), question_id),
            )

    def get_question(self, token: str, question_id: str) -> dict[str, Any] | None:
        session = self.get(token)
        if session is None:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT question_id,status,answer,detail,created_at,completed_at FROM dashboard_questions WHERE question_id=? AND session_id=?",
                (question_id, session["session_id"]),
            ).fetchone()
        return dict(row) if row else None


def _session_or_404(store: DashboardStore, token: str) -> dict[str, Any]:
    session = store.get(token)
    if session is None:
        raise HTTPException(status_code=404, detail="Dashboard session not found or expired")
    return session


def _root(session: dict[str, Any]) -> Path:
    root = Path(session["run_dir"]).resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Workflow directory is unavailable")
    return root


def _safe_files(root: Path) -> list[dict[str, Any]]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.endswith(".save") or part in SKIP_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() not in SAFE_TEXT_SUFFIXES | SAFE_IMAGE_SUFFIXES and path.name not in SAFE_VASP_NAMES:
            continue
        file_id = hashlib.sha256(str(relative).encode("utf-8")).hexdigest()[:24]
        files.append({
            "id": file_id,
            "name": path.name,
            "path": str(relative),
            "size": path.stat().st_size,
            "text": path.suffix.lower() in SAFE_TEXT_SUFFIXES or path.name in SAFE_VASP_NAMES,
        })
        if len(files) >= 500:
            break
    return files


def _resolve_file(session: dict[str, Any], file_id: str) -> tuple[Path, str]:
    root = _root(session)
    match = next((item for item in _safe_files(root) if secrets.compare_digest(item["id"], file_id)), None)
    if match is None:
        raise HTTPException(status_code=404, detail="File is not available")
    target = (root / match["path"]).resolve()
    if target == root or root not in target.parents or target.is_symlink():
        raise HTTPException(status_code=404, detail="File is not available")
    return target, match["path"]


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def session_snapshot(store: DashboardStore, token: str) -> dict[str, Any]:
    session = _session_or_404(store, token)
    root = _root(session)
    state = _load_json(root / "workflow_state.json", {})
    plan = _load_json(root / "workflow_plan.json", [])
    approval = json.loads(session["approval_json"]) if session.get("approval_json") else None
    display_step_status = lambda step: str(step.get("status") or "pending")
    try:
        from workflow_monitor import _display_step_status, _structure_text, _workflow_capabilities
        display_step_status = _display_step_status
        structure = _structure_text(root)
        capabilities = sorted(_workflow_capabilities(root))
    except Exception:
        structure, capabilities = "Structure information is unavailable.", []
    from dashboard_activity import read_activity
    activity = read_activity(root)
    terminal_required = next(
        (item for item in reversed(activity) if item.get("state") in {"waiting_terminal", "success", "error"}),
        None,
    )
    return {
        "session_id": session["session_id"],
        "owner": session["owner"],
        "workflow": root.name,
        "status": state.get("status", "preparing"),
        "updated_at": state.get("updated_at", ""),
        "steps": [
            {**step, "status": display_step_status(step)}
            for step in state.get("steps", [])
        ],
        "plan": state.get("plan", plan),
        "validation": (root / "validation_report.txt").read_text(encoding="utf-8", errors="replace") if (root / "validation_report.txt").is_file() else "No validation report yet.",
        "structure": structure,
        "capabilities": capabilities,
        "files": _safe_files(root),
        "approval": approval,
        "activity": activity,
        "terminal_required": bool(terminal_required and terminal_required.get("state") == "waiting_terminal"),
        "event_version": session["event_version"],
        "expires_at": session["expires_at"],
    }


class FileSave(BaseModel):
    content: str
    expected_sha256: str


class Decision(BaseModel):
    action: str
    revision: str = ""
    max_nodes: int | None = Field(default=None, ge=1)
    cores_per_node: int | None = Field(default=None, ge=1)
    structure_source: str = ""
    structure_path: str = ""


class Message(BaseModel):
    text: str


class Question(BaseModel):
    question: str
    include_failed: bool = False
    consent: bool = False


class VestaLocation(BaseModel):
    path: str = Field(max_length=4096)


def render_workflow_plot(root: Path, kind: str, options: dict[str, Any], output_format: str = "png") -> bytes:
    """Render the same parsed data as Tk, using a headless Matplotlib canvas."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from workflow_monitor import (
        _bands_data, _band_symmetry_ticks, _dos_data, _pdos_data,
        _phonon_dispersion_data, _phonon_path_ticks, _raman_mode_data,
    )
    from results.electronic_reference import electronic_reference
    from results.raman_plot import broaden_raman_modes

    if kind not in {"bands", "dos", "pdos", "phonons", "raman"}:
        raise ValueError("Unknown plot type")
    figure = Figure(figsize=(8.8, 5.8), dpi=120)
    axes = figure.add_subplot(111)
    reference_name = str(options.get("reference") or "VBM")
    reference = electronic_reference(root, reference_name.lower())
    if kind == "bands":
        source = _bands_data(root)
        if not source: raise FileNotFoundError("No downloaded band data found")
        _path, bands = source
        for band in bands:
            axes.plot([p[0] for p in band], [p[1] - reference for p in band], color="black", lw=.9)
        axes.axhline(0, color="tab:red", ls="--", lw=.8)
        ticks = _band_symmetry_ticks(root)
        if ticks:
            positions, labels = zip(*ticks); axes.set_xticks(positions); axes.set_xticklabels(labels)
            for position in positions: axes.axvline(position, color=".75", lw=.6)
        axes.set_xlabel("High-symmetry k-path" if ticks else "k-path distance")
        axes.set_ylabel(f"Energy - {reference_name} (eV)"); axes.set_title("Electronic band structure")
    elif kind == "dos":
        source = _dos_data(root)
        if not source: raise FileNotFoundError("No downloaded DOS data found")
        _path, rows = source; energy = [row[0] - reference for row in rows]
        if min(map(len, rows)) >= 4:
            axes.plot(energy, [r[1] for r in rows], label="spin up"); axes.plot(energy, [-r[2] for r in rows], label="spin down"); axes.legend()
        else: axes.plot(energy, [r[1] for r in rows], color="black")
        axes.axvline(0, color="tab:red", ls="--", lw=.8); axes.set_xlabel(f"Energy - {reference_name} (eV)"); axes.set_ylabel("DOS (states/eV)"); axes.set_title("Total density of states")
    elif kind == "pdos":
        source = _pdos_data(root)
        if not source: raise FileNotFoundError("No downloaded projected-DOS data found")
        _paths, series = source
        for label, raw_energy, up, down in series:
            energy = [value - reference for value in raw_energy]; axes.plot(energy, up, label=label)
            if down is not None: axes.plot(energy, [-v for v in down], ls="--", label=f"{label} down")
        axes.axvline(0, color="tab:red", ls="--", lw=.8); axes.set_xlabel(f"Energy - {reference_name} (eV)"); axes.set_ylabel("Projected DOS (states/eV)"); axes.set_title("Orbital-projected density of states"); axes.legend(fontsize=8, ncol=2)
    elif kind == "phonons":
        source = _phonon_dispersion_data(root)
        if not source: raise FileNotFoundError("No downloaded phonon dispersion data found")
        _path, distance, branches, qpoints = source
        for branch in branches: axes.plot(distance, branch, color="black", lw=.9)
        axes.axhline(0, color="tab:red", ls="--", lw=.8); ticks = _phonon_path_ticks(root, distance, qpoints)
        if ticks:
            positions, labels = zip(*ticks); axes.set_xticks(positions); axes.set_xticklabels(labels)
            for position in positions: axes.axvline(position, color=".72", lw=.7)
        axes.set_xlabel("High-symmetry q path" if ticks else "q-path distance"); axes.set_ylabel("Frequency (cm⁻¹)"); axes.set_title("Phonon dispersion")
    else:
        source = _raman_mode_data(root)
        if not source: raise FileNotFoundError("No downloaded Raman data found")
        _path, modes, _activities, _filtered = source
        linewidth = float(options.get("fwhm") or 8)
        if linewidth <= 0: raise ValueError("Raman FWHM must be greater than zero")
        x, intensity = broaden_raman_modes(modes, linewidth_cm1=linewidth); axes.plot(x, intensity, color="black", lw=1.2)
        axes.set_xlabel("Raman shift (cm⁻¹)"); axes.set_ylabel("Normalized Raman intensity"); axes.set_title("Simulated Raman spectrum")
    for name, setter in (("xmin", "left"), ("xmax", "right")):
        if options.get(name) not in (None, ""): axes.set_xlim(**{setter: float(options[name])})
    for name, setter in (("ymin", "bottom"), ("ymax", "top")):
        if options.get(name) not in (None, ""): axes.set_ylim(**{setter: float(options[name])})
    figure.tight_layout(); data = io.BytesIO(); figure.savefig(data, format=output_format, dpi=180, bbox_inches="tight")
    return data.getvalue()


PAGE = r"""<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>TritonDFT experimental dashboard</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f3f6fa;color:#172033}header{background:#17365d;color:white;padding:18px 24px}main{padding:18px;max-width:1200px;margin:auto}.card{background:white;border:1px solid #d7deea;border-radius:8px;padding:14px;margin-bottom:14px}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #dde3ec;padding:7px;text-align:left}textarea{box-sizing:border-box;width:100%;min-height:360px;font-family:ui-monospace,monospace}button{padding:8px 12px;margin:4px;background:#2563eb;color:white;border:0;border-radius:5px}button:disabled{opacity:.5}button.danger{background:#b91c1c}button.secondary{background:#53657d}.tabs button{background:#e5eaf2;color:#172033}.muted{color:#667085}.error{color:#b91c1c;white-space:pre-wrap}.ok{color:#067647}.terminal-banner{background:#fff4ce;border:2px solid #d97706;color:#7c2d12}.activity{max-height:360px;overflow:auto;background:#101828;color:#e4e7ec;padding:12px;font-family:ui-monospace,monospace}.activity-line{padding:4px 0;border-bottom:1px solid #344054}pre{white-space:pre-wrap;overflow:auto}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style>
</head><body><header><h1>TritonDFT experimental browser dashboard</h1><div id='identity'></div></header><main>
<div class='card'><b>Connection:</b> <span id='connection'>connecting</span> · <b>Status:</b> <span id='status'>preparing</span> · <b>Last updated:</b> <span id='last-updated'>waiting</span> · <span id='next-refresh'>refresh in 15s</span><div class='muted'>Closing this tab does not cancel an HPC calculation.</div></div>
<div id='terminal-banner' class='card terminal-banner' style='display:none'><h2>Action required in terminal</h2><div>SSH authentication is waiting for your password and/or TOTP in the terminal that started TritonDFT. Credentials cannot be entered in this dashboard.</div></div>
<div id='approval' class='card' style='display:none'><h2>Approval required</h2><div id='approval-errors' class='error'></div><pre id='approval-plan'></pre><div id='approval-inputs'></div><div id='approval-extra'></div><textarea id='revision' placeholder='Revision request'></textarea><br><button id='approve-button' onclick="decide('approve')">Approve</button><button class='secondary' onclick="decide('revise')">Request revision</button><button class='danger' onclick="decide('cancel')">Cancel</button><div id='approval-result'></div></div>
<div class='grid'><div class='card'><h2>Workflow</h2><table><thead><tr><th>ID</th><th>Task</th><th>Branch</th><th>Status</th><th>Jobs</th></tr></thead><tbody id='steps'></tbody></table></div><div class='card'><h2>Validation</h2><pre id='validation'></pre></div></div>
<div class='card'><h2>Activity</h2><div class='muted'>Read-only sanitized workflow activity. Passwords, TOTP codes, API keys, and dashboard tokens are never shown.</div><div id='activity' class='activity'>No activity recorded yet.</div></div>
<div class='card'><h2>Structure</h2><button onclick='openVesta()'>Open in VESTA</button><button class='secondary' onclick='locateVesta()'>Locate VESTA…</button><button class='secondary' onclick="window.open('https://jp-minerals.org/vesta/en/download.html','_blank','noopener')">Download VESTA</button><button class='secondary' onclick="location.href='/api/s/'+token+'/structure.cif'">Save CIF As…</button><button class='secondary' onclick='openStructureFolder()'>Open folder</button><div id='structure-result' class='muted'></div><pre id='structure'></pre></div>
<div id='file-card' class='card'><h2>Input and result files</h2><select id='files'></select><button onclick='openFile()'>Open</button><span id='file-note' class='muted'></span><textarea id='editor' readonly></textarea><button id='save' style='display:none' onclick='saveFile()'>Save explicitly</button><div id='save-result'></div></div>
<div class='card'><h2>Interactive result plots</h2><select id='plot-kind'></select> <label>X min <input id='xmin' size='6'></label> <label>X max <input id='xmax' size='6'></label> <label>Y min <input id='ymin' size='6'></label> <label>Y max <input id='ymax' size='6'></label> <label>Reference <select id='reference'><option>VBM</option><option>Fermi</option><option>Midgap</option><option>Absolute</option></select></label> <label>Raman FWHM <input id='fwhm' size='5' value='8'></label><br><button onclick='showPlot()'>Apply</button><button class='secondary' onclick='resetPlot()'>Reset</button><button class='secondary' onclick="downloadPlot('png')">Save PNG</button><button class='secondary' onclick="downloadPlot('pdf')">Save PDF</button><div id='plot-error' class='error'></div><div><img id='plot' style='max-width:100%'></div></div>
<div class='card'><h2>Ask Results</h2><div class='muted'>Searches only this workflow. Relevant excerpts may be sent to your configured LLM provider.</div><input id='question' style='width:65%;padding:8px' placeholder='Question about these calculated files'> <select id='question-scope'><option value='false'>Completed results</option><option value='true'>Include failed attempts</option></select> <label><input id='consent' type='checkbox'> I consent to sending relevant excerpts</label><button onclick='askResults()'>Ask</button><div id='question-status' class='muted'></div><pre id='answer'></pre></div>
<div class='card'><h2>Send a session message</h2><input id='message' style='width:70%;padding:8px' placeholder='Message for the terminal session'><button onclick='sendMessage()'>Send</button><span id='message-result'></span></div>
</main><script>
const token=location.pathname.split('/').pop();let snapshot=null,currentFile=null,currentHash='';const reviewedInputs=new Set();
async function api(path,opt){const r=await fetch('/api/s/'+token+path,opt);if(!r.ok)throw new Error(await r.text());return r.headers.get('content-type')?.includes('json')?r.json():r.text()}
function render(s){snapshot=s;document.getElementById('identity').textContent='Session '+s.session_id+' · '+s.workflow;document.getElementById('status').textContent=s.status;document.getElementById('last-updated').textContent=new Date().toLocaleTimeString();document.getElementById('terminal-banner').style.display=s.terminal_required?'block':'none';const activity=document.getElementById('activity'),wasAtBottom=activity.scrollHeight-activity.scrollTop-activity.clientHeight<30;activity.innerHTML=(s.activity||[]).length?(s.activity||[]).map(x=>`<div class='activity-line'><span class='muted'>${new Date(x.timestamp*1000).toLocaleTimeString()}</span> ${escapeHtml(x.event)}${x.detail?' — '+escapeHtml(x.detail):''}</div>`).join(''):'No activity recorded yet.';if(wasAtBottom)activity.scrollTop=activity.scrollHeight;document.getElementById('validation').textContent=s.validation;document.getElementById('structure').textContent=s.structure||'';document.getElementById('steps').innerHTML=(s.steps||[]).map(x=>`<tr><td>${x.id??''}</td><td>${escapeHtml(x.problem||x.tool||'')}</td><td>${escapeHtml(x.branch||'')}</td><td>${escapeHtml(x.status||'')}</td><td>${escapeHtml((x.job_ids||[]).join(', '))}</td></tr>`).join('');const select=document.getElementById('files'),old=select.value;select.innerHTML=(s.files||[]).map(f=>`<option value='${f.id}'>${escapeHtml(f.path)}</option>`).join('');if([...select.options].some(o=>o.value===old))select.value=old;const kind=document.getElementById('plot-kind'),oldKind=kind.value;kind.innerHTML=(s.capabilities||[]).filter(x=>['bands','dos','pdos','phonons','raman'].includes(x)).map(x=>`<option value='${x}'>${x.toUpperCase()}</option>`).join('');if([...kind.options].some(o=>o.value===oldKind))kind.value=oldKind;const box=document.getElementById('approval');box.style.display=s.approval?'block':'none';if(s.approval){const errors=s.approval.validation_errors||[];document.getElementById('approval-errors').textContent=errors.length?'Approval blocked by validation:\n'+errors.join('\n'):'';if(errors.length)document.getElementById('approval-result').textContent='Correct the listed input problem, then approve again.';document.getElementById('approval-plan').textContent=s.approval.plan||'';renderApprovalInputs(s);const d=s.approval.structure_defaults||{},mp=d.materials_project_available!==false;document.getElementById('approval-extra').innerHTML=s.approval.review_stage==='plan'?"<label>Maximum nodes <input id='nodes' type='number' min='1' value='"+(s.approval.resource_defaults?.max_nodes||1)+"'></label> <label>Cores per node <input id='cores' type='number' min='1' value='"+(s.approval.resource_defaults?.cores_per_node||1)+"'></label><br><label>Structure source <select id='structure-source'>"+(mp?"<option value='materials_project'>Materials Project</option>":"")+"<option value='file'>Workflow-local structure file</option></select></label> <input id='structure-path' placeholder='path inside this workflow directory'>":''}}
function renderApprovalInputs(s){const paths=s.approval.editable_files||[],files=s.files||[],target=document.getElementById('approval-inputs'),approve=document.getElementById('approve-button');if(!paths.length){target.innerHTML=s.approval.review_stage==='inputs'?"<div class='error'>No editable generated inputs were supplied. Do not approve.</div>":'';approve.disabled=s.approval.review_stage==='inputs';return}const rows=paths.map(path=>{const file=files.find(f=>f.path===path),seen=file&&reviewedInputs.has(file.id);return file?`<button class='${seen?'secondary':''}' onclick="reviewApprovalFile('${file.id}')">${seen?'Reviewed':'Review input'}: ${escapeHtml(path)}</button>`:`<div class='error'>Missing generated input: ${escapeHtml(path)}</div>`}).join('');target.innerHTML=`<h3>Generated inputs (${paths.length})</h3><div class='muted'>Open every input before approval. Editable files can be saved explicitly below.</div>${rows}`;approve.disabled=paths.some(path=>{const file=files.find(f=>f.path===path);return !file||!reviewedInputs.has(file.id)})}
function escapeHtml(v){return String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
async function refresh(){try{render(await api(''));document.getElementById('connection').textContent='connected'}catch(e){document.getElementById('connection').textContent='unavailable'}}
async function openFile(){const id=document.getElementById('files').value;if(!id)return;const d=await api('/file/'+id);currentFile=id;currentHash=d.sha256;document.getElementById('editor').value=d.content;document.getElementById('editor').readOnly=!d.editable;document.getElementById('save').style.display=d.editable?'inline-block':'none';document.getElementById('file-note').textContent=d.editable?'Editable approval draft':'Read-only evidence'}
async function reviewApprovalFile(id){document.getElementById('files').value=id;await openFile();reviewedInputs.add(id);renderApprovalInputs(snapshot);document.getElementById('file-card').scrollIntoView({behavior:'smooth',block:'start'})}
async function saveFile(){try{const d=await api('/file/'+currentFile,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({content:document.getElementById('editor').value,expected_sha256:currentHash})});currentHash=d.sha256;document.getElementById('save-result').textContent='Saved explicitly'}catch(e){document.getElementById('save-result').textContent=e}}
async function decide(action){const body={action,revision:document.getElementById('revision').value};if(document.getElementById('nodes')){body.max_nodes=Number(document.getElementById('nodes').value);body.cores_per_node=Number(document.getElementById('cores').value);body.structure_source=document.getElementById('structure-source').value;body.structure_path=document.getElementById('structure-path').value}try{await api('/decision',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});document.getElementById('approval-result').textContent='Decision sent'}catch(e){document.getElementById('approval-result').textContent=e}}
async function sendMessage(){await api('/message',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:document.getElementById('message').value})});document.getElementById('message-result').textContent='Sent'}
async function structureAction(path,opt){const result=document.getElementById('structure-result');result.textContent='Working…';try{const d=await api(path,opt);result.textContent=d.detail||'Done'}catch(e){result.textContent=e}}
function openVesta(){structureAction('/structure/open-vesta',{method:'POST'})}
function openStructureFolder(){structureAction('/structure/open-folder',{method:'POST'})}
function locateVesta(){const path=prompt('Enter the VESTA.app path on the computer running this dashboard.\nExample: /Applications/VESTA.app');if(path)structureAction('/structure/vesta-location',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path})})}
function plotUrl(format='png',download=false){const value=id=>document.getElementById(id).value;const q=new URLSearchParams({xmin:value('xmin'),xmax:value('xmax'),ymin:value('ymin'),ymax:value('ymax'),reference:value('reference'),fwhm:value('fwhm'),format,download:String(download)});return '/api/s/'+token+'/plot/'+value('plot-kind')+'?'+q}
async function showPlot(){const img=document.getElementById('plot');document.getElementById('plot-error').textContent='';try{const r=await fetch(plotUrl());if(!r.ok)throw new Error(await r.text());img.src=URL.createObjectURL(await r.blob())}catch(e){document.getElementById('plot-error').textContent=e}}
function resetPlot(){for(const id of ['xmin','xmax','ymin','ymax'])document.getElementById(id).value='';document.getElementById('reference').value='VBM';document.getElementById('fwhm').value='8';showPlot()}
function downloadPlot(format){location.href=plotUrl(format,true)}
async function askResults(){document.getElementById('question-status').textContent='Searching workflow evidence…';document.getElementById('answer').textContent='Working…';try{const d=await api('/questions',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question:document.getElementById('question').value,include_failed:questionScope(),consent:document.getElementById('consent').checked})});pollQuestion(d.question_id)}catch(e){document.getElementById('question-status').textContent=e;document.getElementById('answer').textContent=''}}
function questionScope(){return document.getElementById('question-scope').value==='true'}
async function pollQuestion(id){const d=await api('/questions/'+id);if(d.status==='working'){setTimeout(()=>pollQuestion(id),1000);return}document.getElementById('question-status').textContent=d.detail||d.status;document.getElementById('answer').textContent=d.answer||''}
let refreshRemaining=15;setInterval(()=>{refreshRemaining--;if(refreshRemaining<=0){refreshRemaining=15;refresh()}document.getElementById('next-refresh').textContent='refresh in '+refreshRemaining+'s'},1000);const ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/'+token);ws.onopen=()=>document.getElementById('connection').textContent='live';ws.onmessage=e=>{render(JSON.parse(e.data));refreshRemaining=15};ws.onerror=()=>document.getElementById('connection').textContent='15-second polling';ws.onclose=()=>document.getElementById('connection').textContent='15-second polling';refresh();
</script></body></html>"""


def create_app(db_path: str | Path | None = None) -> FastAPI:
    store = DashboardStore(db_path or os.environ.get("TRITONDFT_DASHBOARD_DB", "tmp/dashboard_sessions.sqlite3"))
    app = FastAPI(title="TritonDFT experimental browser dashboard")
    app.state.dashboard_store = store

    @app.get("/dashboard-healthz")
    async def health():
        store.cleanup()
        return {
            "status": "ok",
            "api_version": DASHBOARD_API_VERSION,
            "registry_id": hashlib.sha256(str(store.path).encode()).hexdigest()[:16],
        }

    @app.get("/s/{token}", response_class=HTMLResponse)
    async def page(token: str):
        session = _session_or_404(store, token)
        LOG.info("user connected id=%s", session["session_id"])
        return HTMLResponse(PAGE)

    @app.get("/api/s/{token}")
    async def snapshot(token: str):
        return session_snapshot(store, token)

    @app.get("/api/s/{token}/file/{file_id}")
    async def read_file(token: str, file_id: str):
        session = _session_or_404(store, token)
        target, relative = _resolve_file(session, file_id)
        if target.suffix.lower() not in SAFE_TEXT_SUFFIXES and target.name not in SAFE_VASP_NAMES:
            raise HTTPException(status_code=415, detail="File is not text")
        approval = json.loads(session["approval_json"]) if session.get("approval_json") else {}
        editable = relative in set(approval.get("editable_files", []))
        content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > 5_000_000:
            raise HTTPException(status_code=413, detail="Text preview exceeds 5 MB")
        LOG.info("file opened id=%s file=%s", session["session_id"], relative)
        return {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(), "editable": editable}

    @app.put("/api/s/{token}/file/{file_id}")
    async def save_file(token: str, file_id: str, body: FileSave):
        session = _session_or_404(store, token)
        target, relative = _resolve_file(session, file_id)
        approval = json.loads(session["approval_json"]) if session.get("approval_json") else {}
        if relative not in set(approval.get("editable_files", [])) or not (
            target.suffix.lower() == ".in" or target.name in SAFE_VASP_NAMES
        ):
            raise HTTPException(status_code=403, detail="This file is read-only")
        if len(body.content.encode("utf-8")) > 5_000_000:
            raise HTTPException(status_code=413, detail="Edited file exceeds 5 MB")
        old = target.read_text(encoding="utf-8", errors="replace")
        if not secrets.compare_digest(hashlib.sha256(old.encode()).hexdigest(), body.expected_sha256):
            raise HTTPException(status_code=409, detail="File changed after it was opened")
        target.write_text(body.content.rstrip() + "\n", encoding="utf-8")
        digest = hashlib.sha256((body.content.rstrip() + "\n").encode()).hexdigest()
        LOG.info("file saved id=%s file=%s", session["session_id"], relative)
        return {"ok": True, "sha256": digest}

    @app.get("/api/s/{token}/image/{file_id}")
    async def image(token: str, file_id: str):
        session = _session_or_404(store, token)
        target, _relative = _resolve_file(session, file_id)
        if target.suffix.lower() not in SAFE_IMAGE_SUFFIXES:
            raise HTTPException(status_code=415, detail="File is not an image")
        return FileResponse(target, media_type="image/png")

    @app.get("/api/s/{token}/plot/{kind}")
    async def plot(
        token: str,
        kind: str,
        xmin: str = "",
        xmax: str = "",
        ymin: str = "",
        ymax: str = "",
        reference: str = "VBM",
        fwhm: str = "8",
        format: str = "png",
        download: bool = False,
    ):
        session = _session_or_404(store, token)
        root = _root(session)
        if reference not in {"VBM", "Fermi", "Midgap", "Absolute"}:
            raise HTTPException(status_code=400, detail="Invalid energy reference")
        if format not in {"png", "pdf"}:
            raise HTTPException(status_code=400, detail="Invalid plot format")
        try:
            data = await asyncio.to_thread(render_workflow_plot, root, kind, {
                "xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax,
                "reference": reference, "fwhm": fwhm,
            }, format)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        headers = {}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="tritondft-{kind}.{format}"'
        LOG.info("graph updated id=%s kind=%s", session["session_id"], kind)
        return Response(data, media_type="application/pdf" if format == "pdf" else "image/png", headers=headers)

    @app.get("/api/s/{token}/structure.cif")
    async def structure_cif(token: str):
        session = _session_or_404(store, token)
        root = _root(session)
        try:
            from workflow_monitor import _relaxed_structure_to_cif
            path = await asyncio.to_thread(_relaxed_structure_to_cif, root)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return FileResponse(path, media_type="chemical/x-cif", filename="relaxed_structure.cif")

    @app.post("/api/s/{token}/structure/vesta-location")
    async def save_vesta_location(token: str, body: VestaLocation):
        session = _session_or_404(store, token)
        from workflow_monitor import _resolve_vesta_location, _save_vesta_path
        resolved = await asyncio.to_thread(_resolve_vesta_location, Path(body.path))
        if not resolved:
            raise HTTPException(status_code=400, detail="That location does not contain VESTA.app or a VESTA executable")
        await asyncio.to_thread(_save_vesta_path, _root(session), resolved)
        return {"ok": True, "detail": f"VESTA location saved: {resolved}"}

    @app.post("/api/s/{token}/structure/open-vesta")
    async def open_vesta(token: str):
        session = _session_or_404(store, token)
        root = _root(session)
        from workflow_monitor import _find_vesta, _launch_vesta, _relaxed_structure_to_cif
        try:
            cif = await asyncio.to_thread(_relaxed_structure_to_cif, root)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        application = await asyncio.to_thread(_find_vesta, root)
        if not application:
            raise HTTPException(
                status_code=409,
                detail="VESTA was not found on the dashboard computer. Locate it above, or save the CIF and open it on this Mac.",
            )
        try:
            await asyncio.to_thread(_launch_vesta, application, cif)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not open VESTA: {exc}")
        return {"ok": True, "detail": "Opened the relaxed structure in VESTA on the dashboard computer"}

    @app.post("/api/s/{token}/structure/open-folder")
    async def open_structure_folder(token: str):
        session = _session_or_404(store, token)
        root = _root(session)
        import subprocess
        import sys
        try:
            if sys.platform == "darwin":
                await asyncio.to_thread(subprocess.Popen, ["open", str(root)])
            elif os.name == "nt":
                await asyncio.to_thread(subprocess.Popen, ["explorer", str(root)])
            else:
                await asyncio.to_thread(subprocess.Popen, ["xdg-open", str(root)])
        except OSError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"The workflow folder cannot be opened on the dashboard computer: {exc}",
            )
        return {"ok": True, "detail": f"Opened workflow folder on the dashboard computer: {root}"}

    @app.post("/api/s/{token}/decision")
    async def decision(token: str, body: Decision):
        if body.action not in {"approve", "revise", "cancel"}:
            raise HTTPException(status_code=400, detail="Invalid action")
        value = body.dict()
        if body.action == "revise" and not body.revision.strip():
            raise HTTPException(status_code=400, detail="Revision text is required")
        if not store.submit_decision(token, value):
            raise HTTPException(status_code=409, detail="No pending approval or decision already submitted")
        return {"ok": True}

    @app.post("/api/s/{token}/message")
    async def message(token: str, body: Message):
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="Message is required")
        try:
            store.post_message(token, body.text.strip())
        except KeyError:
            raise HTTPException(status_code=404, detail="Session unavailable")
        return {"ok": True}

    def answer_in_background(question_id: str, root: Path, question: str, include_failed: bool) -> None:
        try:
            from results.workflow_qa import answer_workflow_question
            answer, detail = answer_workflow_question(root, question, include_failed=include_failed)
            store.complete_question(question_id, answer, detail)
        except Exception as exc:
            store.complete_question(
                question_id,
                f"The evidence search completed, but the answer could not be generated.\n\n{exc}",
                "Question failed; no unsupported answer was shown.",
                "failed",
            )

    @app.post("/api/s/{token}/questions")
    async def ask_question(token: str, body: Question, background: BackgroundTasks):
        session = _session_or_404(store, token)
        if not body.question.strip():
            raise HTTPException(status_code=400, detail="Question is required")
        if not body.consent:
            raise HTTPException(status_code=400, detail="Consent is required before sending relevant excerpts to the configured LLM")
        question_id = store.create_question(token, body.question.strip()[:4000])
        background.add_task(answer_in_background, question_id, _root(session), body.question.strip(), body.include_failed)
        return {"question_id": question_id, "status": "working"}

    @app.get("/api/s/{token}/questions/{question_id}")
    async def question_status(token: str, question_id: str):
        result = store.get_question(token, question_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Question not found")
        return result

    @app.websocket("/ws/{token}")
    async def websocket(websocket: WebSocket, token: str):
        session = store.get(token)
        if session is None:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        LOG.info("websocket connected id=%s", session["session_id"])
        last = None
        try:
            while True:
                snapshot_value = session_snapshot(store, token)
                encoded = _json(snapshot_value)
                if encoded != last:
                    await websocket.send_text(encoded)
                    last = encoded
                await asyncio.sleep(2)
        except (WebSocketDisconnect, RuntimeError):
            LOG.info("websocket disconnected id=%s", session["session_id"])

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Shared TritonDFT experimental dashboard service")
    parser.add_argument("--host", default=os.environ.get("TRITONDFT_DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TRITONDFT_DASHBOARD_PORT", "8008")))
    parser.add_argument("--db", default=os.environ.get("TRITONDFT_DASHBOARD_DB", "tmp/dashboard_sessions.sqlite3"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import uvicorn
    # Access logs include URL paths, and session URLs contain bearer tokens.
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
