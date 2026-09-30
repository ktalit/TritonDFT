"""Installed command-line entry point for TritonDFT."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import shutil
import stat
import sys
from pathlib import Path

import yaml

from user_paths import config_file, create_config, create_layout, home_dir, slurm_dir


def _version() -> str:
    try:
        return importlib.metadata.version("tritondft")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _write_slurm_templates() -> list[Path]:
    qe_path = slurm_dir() / "my-cluster-qe.sh"
    vasp_path = slurm_dir() / "my-cluster-vasp.sh"
    templates = {
        qe_path: (
            "#!/bin/bash\n"
            "# Edit the partition, account, and modules for your cluster.\n"
            "#SBATCH --partition=shared\n#SBATCH --nodes=1\n"
            "#SBATCH --tasks-per-node=1\n#SBATCH -t 00:10:00\n"
            "#SBATCH -o qe.out\n#SBATCH -e qe.err\n"
            "#SBATCH --job-name=tritondft-qe\n\n"
            "module load quantum-espresso\n\n"
            "exe=pw.x\nINPUT=input.in\nOUTPUT=output.out\n"
            "mpirun -np 1 $exe -in $INPUT > $OUTPUT\n"
        ),
        vasp_path: (
            "#!/bin/bash\n"
            "# Edit the partition, account, and modules for your cluster.\n"
            "#SBATCH --partition=shared\n#SBATCH --nodes=1\n"
            "#SBATCH --tasks-per-node=1\n#SBATCH -t 01:00:00\n"
            "#SBATCH -o vasp.out\n#SBATCH -e vasp.err\n"
            "#SBATCH --job-name=tritondft-vasp\n\n"
            "module load vasp\n\n"
            "exe=vasp_std\nOUTPUT=vasp.out\nmpirun -np 1 $exe > $OUTPUT\n"
        ),
    }
    created: list[Path] = []
    for path, content in templates.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            path.chmod(0o700)
            created.append(path)
    return created


def init_command() -> int:
    create_layout()
    path, created = create_config()
    templates = _write_slurm_templates()
    print(f"TritonDFT user directory: {home_dir()}")
    print(f"{'Created' if created else 'Kept existing'} configuration: {path}")
    for template in templates:
        print(f"Created Slurm template: {template}")
    print(f"Next: edit {path}, then run `tritondft doctor`.")
    return 0


def _configured_value(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and not any(
        marker in text
        for marker in ("your_cluster_user", "login.example.edu", "/path/to/your")
    )


def doctor_command() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    if sys.version_info >= (3, 11):
        checks.append(f"Python {sys.version_info.major}.{sys.version_info.minor}")
    else:
        failures.append("Python 3.11 or newer is required")

    if shutil.which("ssh"):
        checks.append("SSH client")
    else:
        failures.append("ssh was not found on PATH")

    path = config_file()
    if not path.is_file():
        failures.append(f"configuration is missing: {path} (run `tritondft init`)")
    else:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError("top-level YAML value must be a mapping")
            active = str(data.get("active_cluster", "")).strip()
            clusters = data.get("clusters", {}) or {}
            profile = clusters.get(active, {}) if isinstance(clusters, dict) else {}
            if not active or not isinstance(profile, dict) or not profile:
                failures.append("active_cluster does not select a cluster profile")
            else:
                for key in ("user_id", "hostname", "remote_working_directory"):
                    if not _configured_value(profile.get(key)):
                        failures.append(f"configure clusters.{active}.{key}")
                checks.append(f"cluster profile: {active}")
            api_keys = data.get("api_keys", {}) or {}
            if not isinstance(api_keys, dict) or not _configured_value(api_keys.get("openai")):
                warnings.append("OpenAI API key is blank (okay with administrator-managed keys)")
            if not isinstance(api_keys, dict) or not _configured_value(api_keys.get("materials_project")):
                warnings.append("Materials Project API key is blank (okay with administrator-managed keys)")
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                warnings.append(f"configuration permissions are {mode:o}; use chmod 600 {path}")
            else:
                checks.append("private configuration permissions")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            failures.append(f"cannot read configuration: {exc}")

    try:
        create_layout()
        probe = home_dir() / ".write-test"
        probe.touch()
        probe.unlink()
        checks.append("writable user directory")
    except OSError as exc:
        failures.append(f"user directory is not writable: {exc}")

    for message in checks:
        print(f"[OK] {message}")
    for message in warnings:
        print(f"[WARNING] {message}")
    for message in failures:
        print(f"[FAIL] {message}")
    print("TritonDFT is ready." if not failures else "TritonDFT needs configuration changes.")
    return 0 if not failures else 1


def _help_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tritondft",
        description="Interactive local-to-cluster DFT workflow agent",
    )
    parser.add_argument("--version", action="version", version=f"TritonDFT {_version()}")
    parser.add_argument("command", nargs="?", choices=("init", "doctor", "run"))
    return parser


def main() -> int:
    args = sys.argv[1:]
    if args == ["--version"]:
        print(f"TritonDFT {_version()}")
        return 0
    if args and args[0] == "init":
        return init_command()
    if args and args[0] == "doctor":
        return doctor_command()
    if args and args[0] in {"-h", "--help"}:
        _help_parser().print_help()
        return 0
    if args and args[0] == "run":
        sys.argv = [sys.argv[0], *args[1:]]
    from cluster_agent import interactive_main

    interactive_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
