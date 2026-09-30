"""User-owned paths shared by the CLI and runtime."""

from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    override = os.environ.get("TRITONDFT_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".tritondft"


def config_file() -> Path:
    override = os.environ.get("CLUSTER_AGENT_CONFIG_FILE", "").strip()
    return Path(override).expanduser() if override else home_dir() / "config.yaml"


def workflows_dir() -> Path:
    return home_dir() / "workflows"


def slurm_dir() -> Path:
    return home_dir() / "slurm"


def create_layout() -> list[Path]:
    root = home_dir()
    directories = [
        root,
        root / "cache",
        root / "logs",
        root / "pseudopotentials",
        slurm_dir(),
        workflows_dir(),
    ]
    for directory in directories:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return directories


def default_config_text() -> str:
    work_dir = workflows_dir()
    qe_template = slurm_dir() / "my-cluster-qe.sh"
    vasp_template = slurm_dir() / "my-cluster-vasp.sh"
    return (
        "# TritonDFT configuration. Replace the example values before running.\n"
        "active_cluster: my_cluster\n\n"
        "api_keys:\n"
        "  openai: \"\"\n"
        "  materials_project: \"\"\n\n"
        "defaults:\n"
        f"  work_dir: {work_dir}\n"
        "  poll_seconds: 30\n"
        "  dft_code: qe\n\n"
        "clusters:\n"
        "  my_cluster:\n"
        "    user_id: your_cluster_user\n"
        "    hostname: login.example.edu\n"
        "    ssh_alias: \"\"\n"
        "    remote_working_directory: /scratch/$USER/tritondft_runs\n"
        f"    qe_slurm_script: {qe_template}\n"
        "    remote_qe_bin_dir: \"\"\n"
        f"    vasp_slurm_script: {vasp_template}\n"
        "    remote_vasp_command: \"\"\n"
        "    remote_vasp_potcar_root: /path/to/authorized/VASP_PP\n"
        "    vasp_functional: \"\"\n"
    )


def create_config(path: Path | None = None) -> tuple[Path, bool]:
    destination = path or config_file()
    destination = destination.expanduser()
    create_layout()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists():
        return destination, False
    destination.write_text(default_config_text(), encoding="utf-8")
    destination.chmod(0o600)
    return destination, True
