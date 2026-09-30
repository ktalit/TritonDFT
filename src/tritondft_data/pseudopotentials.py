"""Resolve TritonDFT's bundled PseudoDojo libraries."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


PSEUDO_LIBRARY_PATHS = {
    "LDA": ("SR_v0.4.1", "LDA_standard"),
    "PBE": ("SR_v0.4.1", "PBE_standard"),
    "PBESOL": ("SR_v0.4.1", "PBEsol_standard"),
    "PBE_FR": ("FR_v0.4", "PBE_standard"),
    "PBESOL_FR": ("FR_v0.4", "PBEsol_standard"),
}


def packaged_pseudodojo_root() -> Path:
    """Return the installed PseudoDojo resource directory.

    Python installers unpack wheels onto the filesystem, which is required by
    Quantum ESPRESSO because ``pseudo_dir`` must name a real directory.
    """
    resource = files("tritondft_data").joinpath("PseudoDojo")
    path = Path(str(resource)).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Packaged PseudoDojo directory is missing: {path}")
    return path


def packaged_pseudo_dir(family: str) -> Path:
    """Return and validate the bundled directory for one XC/relativity family."""
    try:
        relative_parts = PSEUDO_LIBRARY_PATHS[family.upper()]
    except KeyError as exc:
        choices = ", ".join(sorted(PSEUDO_LIBRARY_PATHS))
        raise ValueError(f"Unknown pseudopotential family {family!r}; choose from {choices}") from exc

    path = packaged_pseudodojo_root().joinpath(*relative_parts)
    if not path.is_dir() or not any(path.glob("*.upf")):
        raise FileNotFoundError(f"Packaged pseudopotential library is missing or empty: {path}")
    return path
