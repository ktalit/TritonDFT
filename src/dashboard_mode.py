"""Mode selection and terminal-side client for optional browser dashboards."""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from browser_dashboard import DASHBOARD_API_VERSION, DashboardStore


VALID_DASHBOARD_MODES = {"x11", "browser", "both"}
_DASHBOARD_MODE_ALIASES = {
    "xwindows": "x11",
    "x-windows": "x11",
    "https": "browser",
}


def dashboard_mode(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    value = source.get("TRITONDFT_DASHBOARD_MODE", "x11").strip().lower()
    value = _DASHBOARD_MODE_ALIASES.get(value, value)
    return value if value in VALID_DASHBOARD_MODES else "x11"


class BrowserDashboardSession:
    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.host = os.environ.get("TRITONDFT_DASHBOARD_HOST", "127.0.0.1")
        self.port = int(os.environ.get("TRITONDFT_DASHBOARD_PORT", "8008"))
        self.db_path = Path(os.environ.get("TRITONDFT_DASHBOARD_DB", "tmp/dashboard_sessions.sqlite3")).expanduser().resolve()
        self.ttl = int(os.environ.get("TRITONDFT_DASHBOARD_SESSION_TTL", "28800"))
        self.store = DashboardStore(self.db_path)
        self.session_id, self.token = self.store.create_session(self.run_dir, getpass.getuser(), self.ttl)
        self._ensure_server()
        configured = os.environ.get("TRITONDFT_DASHBOARD_PUBLIC_URL", "").rstrip("/")
        base = configured or f"http://{self.host}:{self.port}"
        self.url = f"{base}/s/{self.token}"
        print(f"\nBrowser dashboard ready:\n\n{self.url}\n\nOpen this URL in your browser.\n")

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://{self.host}:{self.port}/dashboard-healthz", timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                import hashlib
                expected = hashlib.sha256(str(self.db_path).encode()).hexdigest()[:16]
                return (
                    response.status == 200
                    and payload.get("registry_id") == expected
                    and payload.get("api_version") == DASHBOARD_API_VERSION
                )
        except (OSError, urllib.error.URLError):
            return False

    def _ensure_server(self) -> None:
        if self._healthy():
            return
        script = Path(__file__).with_name("browser_dashboard.py")
        log_path = self.db_path.with_name("browser_dashboard.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            subprocess.Popen(
                [sys.executable, str(script), "--host", self.host, "--port", str(self.port), "--db", str(self.db_path)],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        for _ in range(30):
            if self._healthy():
                return
            time.sleep(0.1)
        raise RuntimeError(
            "Browser dashboard service did not start. An older service may still be using "
            f"{self.host}:{self.port}; stop that listener and retry. See {log_path}"
        )

    def publish_monitor(self) -> None:
        # Session snapshots are built directly from the registered run directory.
        pass

    def request_approval(
        self,
        plan: str,
        input_paths: list[str],
        workflow_validator: Callable[[], list[str]] | None = None,
        *,
        review_stage: str = "inputs",
        workflow_steps: list[dict[str, Any]] | None = None,
        resource_defaults: dict[str, int] | None = None,
        structure_defaults: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        root = self.run_dir
        editable = []
        for value in input_paths:
            path = Path(value).expanduser().resolve()
            if path.is_file() and root in path.parents and (
                path.suffix.lower() == ".in" or path.name in {"POSCAR", "INCAR", "KPOINTS"}
            ):
                editable.append(str(path.relative_to(root)))
        payload = {
            "plan": plan,
            "review_stage": review_stage,
            "workflow_steps": workflow_steps or [],
            "editable_files": editable,
            "resource_defaults": resource_defaults or {},
            "structure_defaults": structure_defaults or {},
            "validation_errors": [],
        }
        self.store.publish_approval(self.token, payload)
        print("[approval] Waiting for a decision in the browser dashboard.")
        while True:
            decision = self.store.take_decision(self.token)
            if decision is None:
                message = self.store.take_message(self.token)
                if message:
                    print(f"[browser] {message['text']}")
                time.sleep(0.25)
                continue
            action = decision.get("action")
            if action == "approve":
                if decision.get("structure_source") == "file":
                    candidate = Path(decision.get("structure_path", "")).expanduser()
                    candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
                    if not candidate.is_file() or root not in candidate.parents:
                        payload["plan"] = plan + (
                            "\n\nBROWSER STRUCTURE ERROR\nThe selected structure must already be a file "
                            "inside this workflow directory."
                        )
                        self.store.publish_approval(self.token, payload)
                        print("[approval] Browser structure path rejected; waiting for a workflow-local file.")
                        continue
                    decision["structure_path"] = str(candidate)
                from utils import normalize_qe_input_text
                for relative in editable:
                    path = (root / relative).resolve()
                    if path.suffix.lower() == ".in":
                        normalized = normalize_qe_input_text(path.read_text(encoding="utf-8"))
                        path.write_text(normalized.rstrip() + "\n", encoding="utf-8")
                errors = list(workflow_validator()) if workflow_validator else []
                if errors:
                    payload["validation_errors"] = errors
                    payload["plan"] = plan + "\n\nBLOCKING VALIDATION ERRORS\n" + "\n".join(errors)
                    self.store.publish_approval(self.token, payload)
                    print("[approval] Blocking validation errors:")
                    for error in errors:
                        print(f"  - {error}")
                    print("[approval] Browser approval rejected by existing validation; waiting for corrected input.")
                    continue
            resources = {}
            if decision.get("max_nodes") is not None:
                resources = {"max_nodes": int(decision["max_nodes"]), "cores_per_node": int(decision["cores_per_node"])}
            return {
                "action": action,
                "revision": decision.get("revision", ""),
                "resources": resources or dict(resource_defaults or {}),
                "structure": {
                    "source": decision.get("structure_source") or (structure_defaults or {}).get("source", "materials_project"),
                    "path": decision.get("structure_path", ""),
                },
            }

    def close(self) -> None:
        self.store.close(self.token)
