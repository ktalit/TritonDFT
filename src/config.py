"""
Minimal pseudopotential configuration loader.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tritondft_data.pseudopotentials import PSEUDO_LIBRARY_PATHS, packaged_pseudo_dir

try:
    import yaml
except ImportError as exc:
    raise ImportError("PyYAML is required to load config.yaml; install it via `pip install pyyaml`.") from exc

DEFAULT_QE_BIN_DIR = "QuantumE/bin"


@dataclass(frozen=True)
class PseudoPaths:
    LDA: str
    PBE: str
    PBESOL: str
    PBE_FR: str
    PBESOL_FR: str

    @classmethod
    def from_dict(cls, data: dict) -> "PseudoPaths":
        if not data:
            data = {}
        return cls(
            LDA=data.get("LDA") or data.get("lda") or "",
            PBE=data.get("PBE") or data.get("pbe") or "",
            PBESOL=data.get("PBESOL") or data.get("pbesol") or "",
            PBE_FR=data.get("PBE_FR") or data.get("pbe_fr") or "",
            PBESOL_FR=data.get("PBESOL_FR") or data.get("pbesol_fr") or "",
        )

    def as_dict(self) -> dict:
        return {
            "lda": self.LDA,
            "pbe": self.PBE,
            "pbesol": self.PBESOL,
            "pbe_fr": self.PBE_FR,
            "pbesol_fr": self.PBESOL_FR,
        }


@dataclass(frozen=True)
class Config:
    pseudo: PseudoPaths
    qe_bin_dir: str
    remote_qe_bin_dir: str
    path: Path

    @classmethod
    def load(cls, config_name: Optional[str] = None) -> "Config":
        repo_root = Path(__file__).resolve().parent.parent
        config_root = repo_root / "config"
        if config_name:
            path = Path(config_name)
            if not path.is_absolute():
                path = config_root / config_name
        else:
            path = config_root / "config.yaml"

        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            pseudo_section = data.get("pseudo", {})
            qe_bin_dir = data.get("qe_bin_dir")
            remote_qe_bin_dir = data.get("remote_qe_bin_dir")
        else:
            pseudo_section = {}
            qe_bin_dir = None
            remote_qe_bin_dir = None

        # Explicit administrator/user overrides remain authoritative. Relative
        # overrides retain their existing source-checkout behavior and resolve
        # from the repository/application root. Unset families use packaged,
        # read-only PseudoDojo resources instead of Python-prefix assumptions.
        pseudo = PseudoPaths.from_dict(pseudo_section)

        def _resolve(family: str, configured: str) -> str:
            if not configured:
                return str(packaged_pseudo_dir(family))
            path = Path(configured).expanduser()
            return str((repo_root / path).resolve()) if not path.is_absolute() else str(path)

        resolved = {
            family: _resolve(family, getattr(pseudo, family))
            for family in PSEUDO_LIBRARY_PATHS
        }

        # Never cross-fallback between XC families or relativistic levels.
        # A missing same-family library must fail clearly instead of silently
        # producing a scientifically inconsistent calculation.
        pseudo = PseudoPaths(**resolved)

        final_qe_bin = qe_bin_dir or str((repo_root / DEFAULT_QE_BIN_DIR).resolve())
        return cls(
            pseudo=pseudo,
            qe_bin_dir=final_qe_bin,
            remote_qe_bin_dir=remote_qe_bin_dir or "",
            path=path,
        )
