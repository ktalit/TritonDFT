"""Small local CLI for opening saved TritonDFT workflows in the dashboard."""
from __future__ import annotations

import argparse
import os

from cluster_agent import (
    _discover_workflows,
    _launch_configured_dashboard,
    _print_workflows,
    _resolve_workflow_to_open,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Browse saved TritonDFT workflows")
    parser.add_argument("--work-dir", default="tmp", help="Directory containing saved workflows")
    parser.add_argument(
        "--dashboard",
        choices=("browser", "https", "x11", "xwindows", "x-windows", "both"),
        default="browser",
        help="Dashboard interface (https is an alias for browser; xwindows is an alias for x11)",
    )
    args = parser.parse_args()
    if args.dashboard == "https" and not os.environ.get(
        "TRITONDFT_DASHBOARD_PUBLIC_URL", ""
    ).strip().lower().startswith("https://"):
        parser.error(
            "--dashboard https requires TRITONDFT_DASHBOARD_PUBLIC_URL to be an https:// URL"
        )
    os.environ["TRITONDFT_DASHBOARD_MODE"] = args.dashboard

    print("TritonDFT saved workflow browser")
    print("Commands: workflows, open <number>, latest, <number>, quit\n")
    _print_workflows(_discover_workflows(args.work_dir))
    while True:
        try:
            entered = input("workflow> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        normalized = entered.lower()
        if normalized in {"quit", "exit", "q"}:
            break
        if normalized in {"workflows", "workflow", "runs", "history", "list", "ls"}:
            _print_workflows(_discover_workflows(args.work_dir))
            continue
        selection = entered[4:].strip() if normalized.startswith("open ") else entered
        if normalized == "open":
            selection = "latest"
        if not selection:
            continue
        run_dir = _resolve_workflow_to_open(selection, args.work_dir)
        if not run_dir:
            print(f"[workflows] Could not resolve {selection!r}. Type 'workflows' to list choices.")
            continue
        print(f"[workflows] Opening browser dashboard: {run_dir}")
        try:
            _launch_configured_dashboard(run_dir)
        except RuntimeError as exc:
            print(f"[dashboard] {exc}")


if __name__ == "__main__":
    main()
