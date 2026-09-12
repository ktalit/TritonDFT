#!/usr/bin/env python3
"""Metadata-plane load probe; it never launches DFT or cluster jobs."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import resource
import statistics
import tempfile
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from browser_dashboard import DashboardStore, session_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, choices=(10, 50, 100, 500, 1000), default=10)
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tritondft-dashboard-load-") as tmp:
        root = Path(tmp)
        store = DashboardStore(root / "sessions.sqlite3")
        tokens = []
        for index in range(args.sessions):
            run = root / f"run-{index}"
            run.mkdir()
            (run / "workflow_state.json").write_text(json.dumps({"status": "running", "steps": [{"id": 1, "status": "running", "job_ids": []}]}))
            (run / "scf.in").write_text("&control\n calculation='scf'\n/\n")
            _session_id, token = store.create_session(run, f"user-{index}", 3600)
            tokens.append(token)

        def activity(token):
            start = time.perf_counter()
            snapshot = session_snapshot(store, token)
            store.post_message(token, "simulated activity")
            return (time.perf_counter() - start) * 1000, len(snapshot["files"])

        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(activity, tokens))
        elapsed = time.perf_counter() - started
        latencies = sorted(value[0] for value in results)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * .95))]
        usage = resource.getrusage(resource.RUSAGE_SELF)
        print(json.dumps({
            "sessions": args.sessions,
            "elapsed_seconds": round(elapsed, 4),
            "operations_per_second": round(args.sessions / elapsed, 2),
            "latency_ms_mean": round(statistics.mean(latencies), 3),
            "latency_ms_p95": round(p95, 3),
            "max_rss_platform_units": usage.ru_maxrss,
            "errors": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
