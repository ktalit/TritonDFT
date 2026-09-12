import datetime
import json
import math
import re
import shlex
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from generator import UnifiedGenerator
from prompt import get_prompt
from tool import fetch_material_info_from_api_snippet
from utils import extract_json_brutal, output_to_log_file
from execute_code.slurm_template import render_slurm_script


VASP_FILE_NAMES = ("POSCAR", "INCAR", "KPOINTS")
VASP_RELAXED_POSCAR_PLACEHOLDER = """TRITONDFT_RELAXED_POSCAR_PLACEHOLDER
This file is intentionally not a crystal structure.
After vc-relax completes, TritonDFT replaces it with the verified CONTCAR.
Do not submit this placeholder to VASP.
"""


def _is_relaxed_poscar_placeholder(path: Path) -> bool:
    try:
        return path.read_text(encoding="utf-8").startswith(
            "TRITONDFT_RELAXED_POSCAR_PLACEHOLDER"
        )
    except OSError:
        return False


@dataclass
class VASPInputSet:
    step_index: int
    title: str
    task: str
    directory: Path
    files: List[str]
    species: List[str]
    output_path: str
    potcar_mode: str = "local"
    slurm_path: str = ""


@dataclass
class VASPRunSettings:
    potcar_functional: str
    vasp_command: str
    reason: str


def _sanitize_name(name: str, max_len: int = 40) -> str:
    name = re.sub(r"[^\w\-]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:max_len] if name else ""


def _step_directory_name(task: str, occurrence: int = 1, *, max_len: int = 40) -> str:
    base = _sanitize_name((task or "vasp").strip(), max_len=max_len)
    if not base:
        base = "vasp"
    if occurrence <= 1:
        return base
    suffix = f"_{occurrence}"
    candidate = f"{base}{suffix}"
    if len(candidate) > max_len:
        candidate = f"{base[: max_len - len(suffix)]}{suffix}"
    return candidate


def _extract_query_metadata(query: str) -> Dict[str, str]:
    material = ""
    match = re.search(r"material\s*=\s*([A-Za-z][A-Za-z0-9]*)", query)
    if match:
        material = match.group(1)
    else:
        match = re.search(
            r"\bfor\s+(?:[\w-]+\s+)?([A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*)\b",
            query,
        )
        if match:
            material = match.group(1)

    task_patterns = [
        (r"\bvc[_-]relax\b|variable[_-]cell\s+relax", "vc-relax"),
        (r"\bnscf\b|non[_-]self[_-]consistent", "nscf"),
        (r"\bscf\b|self[_-]consistent\s+field", "scf"),
        (r"\brelax(?:ation)?\b", "relax"),
        (r"\bband[\s_-]?(?:structure|gap|calculation)", "bands"),
        (r"\bdos\b|density\s+of\s+states", "dos"),
    ]
    tasks: List[str] = []
    for pattern, task in task_patterns:
        if re.search(pattern, query, re.IGNORECASE) and task not in tasks:
            tasks.append(task)
    if "vc-relax" in tasks and "relax" in tasks:
        tasks.remove("relax")
    return {"material_name": material, "task_type": "+".join(tasks)}


def _normalize_potcar_functional(value: str) -> str:
    normalized = (value or "").strip().upper().replace("-", "").replace("_", "")
    aliases = {
        "PBE": "PBE",
        "PBESOL": "PBEsol",
        "PBE0": "PBE",
        "HSE": "PBE",
        "HSE03": "PBE",
        "HSE06": "PBE",
        "HYBRID": "PBE",
        "LDA": "LDA",
        "PW91": "PW91",
        "GGA": "PW91",
    }
    return aliases.get(normalized, "")


def _infer_potcar_functional(query: str) -> str:
    text = (query or "").lower()
    if re.search(r"\bpbe\s*sol\b|\bpbesol\b", text):
        return "PBEsol"
    if re.search(r"\blda\b|local density approximation", text):
        return "LDA"
    if re.search(r"\bpw91\b", text):
        return "PW91"
    if re.search(r"\bhse(?:03|06)?\b|\bhybrid\b|\bpbe0\b", text):
        return "PBE"
    if re.search(r"\bpbe\b", text):
        return "PBE"
    return ""


def _normalize_vasp_command(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    lower = value.lower()
    if re.fullmatch(r"vasp_(?:std|gam|ncl)", lower):
        return lower
    if lower in {"std", "standard"}:
        return "vasp_std"
    if lower in {"gam", "gamma", "gamma-only", "gamma_only"}:
        return "vasp_gam"
    if lower in {"ncl", "noncollinear", "non-collinear", "soc", "spin-orbit"}:
        return "vasp_ncl"
    return value


def _infer_vasp_command(query: str, steps: List[Dict[str, Any]]) -> str:
    text = (query or "").lower()
    if re.search(r"\bsoc\b|spin[-\s]?orbit|non[-\s]?collinear|\blsorbit\b|\bncl\b", text):
        return "vasp_ncl"
    if re.search(r"gamma[-\s]?only|gamma\s+point\s+only|\bvasp_gam\b", text):
        return "vasp_gam"
    if re.search(r"\bvasp_std\b|\bstandard\s+vasp\b", text):
        return "vasp_std"
    for step in steps:
        task = str(step.get("task") or "").lower()
        if task in {"bands", "dos", "nscf"}:
            return "vasp_std"
    return ""


def _generate_nonempty_text(
    generator,
    prompt: str,
    *,
    max_new_tokens: int,
    attempts: int = 3,
    verbose: bool = False,
    purpose: str = "vasp_generation",
) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            result = generator(prompt, max_new_tokens=max_new_tokens, return_full_text=False)
        except Exception as exc:
            last_error = exc
            if verbose:
                print(f"[{purpose}] model call {attempt}/{attempts} failed: {exc}")
        else:
            text = ""
            if result and isinstance(result, list):
                text = result[0].get("generated_text", "") or ""
            if text.strip():
                return text
            if verbose:
                print(f"[{purpose}] model returned an empty response; retrying.")
    if last_error:
        raise RuntimeError(f"{purpose} failed after {attempts} attempts: {last_error}") from last_error
    raise RuntimeError(f"{purpose} returned empty text after {attempts} attempts.")


def _parse_json_object(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    data = extract_json_brutal(text)
    if isinstance(data, dict):
        return data
    match = re.search(r"(?s)\{.*\}", text)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    raise ValueError("The model did not return a JSON object.")


def _vasp_plan_prompt(query: str) -> str:
    return f"""You are a senior VASP workflow planner.

Plan a minimal VASP workflow for the user's DFT request. Use only these task labels:
relax, vc-relax, scf, nscf, bands, dos, static.

Rules:
- Prefer a small, executable sequence.
- Use relax or vc-relax before dependent static/scf/bands/dos steps when relaxation is requested.
- For a simple energy or electronic-structure request, include scf/static as appropriate.
- Do not include Quantum ESPRESSO tools or file names.

Return only JSON with this schema:
{{
  "settings": {{
    "potcar_functional": "<PBE|PBEsol|LDA|PW91 or null when not specified>",
    "vasp_command": "<vasp_std|vasp_gam|vasp_ncl or null when not specified>",
    "reason": "<brief reason for these choices>"
  }},
  "steps": [
    {{"title": "<short step title>", "task": "<task label>", "why": "<short reason>"}}
  ]
}}

User request:
{query}
"""


def _vasp_input_prompt(
    *,
    query: str,
    step: Dict[str, Any],
    step_index: int,
    total_steps: int,
    material_info: Dict[str, Any],
    previous_context: str,
    potcar_root: str,
    functional: str,
    vasp_command: str,
) -> str:
    primitive = material_info.get("primitive_structure", "")
    conventional = material_info.get("conventional_structure", "")
    initial = material_info.get("initial_structures", "")
    return f"""You are a computational materials scientist preparing VASP inputs.

Generate input files for only the current VASP step.

VASP facts to obey:
- POSCAR contains the lattice vectors, species names, counts, and ion positions.
- The POSCAR species order must be the same order used to concatenate POTCAR.
- INCAR is tag = value format. Use VASP tags, not Quantum ESPRESSO namelists.
- KPOINTS controls the k-point sampling.
- POTCAR is proprietary and will be assembled by TritonDFT from the user's licensed potential tree. Do not output POTCAR content.
- Use ENCUT in eV. If you do not know the exact POTCAR ENMAX values, set a conservative placeholder ENCUT.
- Use VASP-appropriate tags such as SYSTEM, ENCUT, EDIFF, ISMEAR, SIGMA, IBRION, NSW, ISIF, EDIFFG, LCHARG, LWAVE, NELM, LORBIT, ICHARG, NBANDS, KPAR, NCORE only when relevant.
- Do not write QE tags such as ecutwfc, ecutrho, prefix, outdir, ATOMIC_SPECIES, ATOMIC_POSITIONS, or K_POINTS.

POTCAR setup:
- Functional requested: {functional}
- Licensed POTCAR root, local or cluster path: {potcar_root}

Executable selection:
- VASP executable selected for this workflow: {vasp_command}
- Use INCAR settings consistent with that executable. For spin-orbit or noncollinear calculations,
  include the necessary VASP tags such as LSORBIT, LNONCOLLINEAR, SAXIS, MAGMOM, or related tags
  only when they are relevant to the user's request.

Workflow context:
- Current step {step_index} of {total_steps}: {step}
- Earlier workflow context: {previous_context or "None"}

Available structure data from Materials Project or user query:
Primitive structure:
{primitive}

Conventional structure:
{conventional}

Initial structures:
{initial}

User request:
{query}

Return only JSON with this schema:
{{
  "POSCAR": "<complete POSCAR text>",
  "INCAR": "<complete INCAR text>",
  "KPOINTS": "<complete KPOINTS text>",
  "notes": "<brief internal note about assumptions>"
}}
"""


def _species_from_poscar(poscar_text: str) -> List[str]:
    lines = [line.strip() for line in poscar_text.splitlines() if line.strip()]
    if len(lines) < 7:
        return []
    species_line = lines[5].split()
    count_line = lines[6].split()
    if species_line and all(re.fullmatch(r"[A-Za-z][a-z]?", token) for token in species_line):
        if count_line and all(re.fullmatch(r"\d+", token) for token in count_line):
            return species_line
    return []


def _potential_root_candidates(root: Path, functional: str) -> List[Path]:
    functional = functional.upper()
    names = {
        "PBE": ["potpaw_PBE", "PBE", "pbe"],
        "PBESOL": ["potpaw_PBEsol", "PBEsol", "pbesol", "PBESOL"],
        "LDA": ["potpaw", "LDA", "lda"],
        "PW91": ["potpaw_GGA", "PW91", "pw91"],
    }.get(functional, [functional, functional.lower()])
    candidates = [root]
    candidates.extend(root / name for name in names)
    return candidates


def _find_potcar_for_species(root: Path, species: str, functional: str) -> Path:
    variants = [
        species,
        f"{species}_pv",
        f"{species}_sv",
        f"{species}_GW",
    ]
    for base in _potential_root_candidates(root, functional):
        for variant in variants:
            candidate = base / variant / "POTCAR"
            if candidate.exists():
                return candidate
    matches: List[Path] = []
    for base in _potential_root_candidates(root, functional):
        if base.exists():
            matches.extend(base.glob(f"{species}*/POTCAR"))
    if matches:
        return sorted(matches, key=lambda p: (len(p.parent.name), p.parent.name))[0]
    raise FileNotFoundError(
        f"Could not find a POTCAR for species '{species}' under {root} "
        f"using functional {functional}."
    )


def _extract_enmax(potcar_text: str) -> List[float]:
    values: List[float] = []
    for match in re.finditer(r"ENMAX\s*=\s*([0-9.]+)", potcar_text, re.IGNORECASE):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            pass
    return values


def _set_or_add_incar_tag(incar_text: str, tag: str, value: str) -> str:
    pattern = re.compile(rf"(?mi)^(\s*{re.escape(tag)}\s*=\s*).*$")
    if pattern.search(incar_text):
        return pattern.sub(rf"\g<1>{value}", incar_text)
    return incar_text.rstrip() + f"\n{tag} = {value}\n"


def _vasp_task_kind(task: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (task or "").lower()).strip("-")
    if normalized in {"vc-relax", "cell-relax", "variable-cell-relax", "optimization"}:
        return "vc-relax"
    if normalized in {"relax", "ionic-relax", "geometry-optimization"}:
        return "relax"
    if normalized in {"bands", "band", "band-structure", "nscf"}:
        return "bands"
    if normalized in {"scf", "static", "single-point"}:
        return "scf"
    return normalized


def _apply_default_vasp_relaxation(
    query: str, steps: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Start new VASP workflows with relaxation unless explicitly declined."""
    text = (query or "").lower()
    explicit_no_relax = re.search(
        r"\b(?:do\s+not|don't|dont|no|without|skip)\s+"
        r"(?:a\s+|the\s+)?(?:vc[-\s]?relax(?:ation)?|relax(?:ation)?|"
        r"geometry\s+optimi[sz]ation)\b|"
        r"\b(?:fixed|unrelaxed)\s+(?:input\s+|supplied\s+|experimental\s+)?geometry\b",
        text,
    )
    if explicit_no_relax:
        return steps
    if any(_vasp_task_kind(str(step.get("task") or "")) in {"vc-relax", "relax"} for step in steps):
        return steps
    constrained = bool(
        re.search(
            r"\b(?:constrained|fixed[-\s]?cell|keep\s+(?:the\s+)?cell\s+fixed)\b",
            text,
        )
    )
    relaxation = {
        "title": "Constrained ionic relaxation" if constrained else "Variable-cell relaxation",
        "task": "relax" if constrained else "vc-relax",
        "why": (
            "Relax ionic positions while keeping the supplied cell fixed, as requested."
            if constrained
            else "Optimize ionic positions, cell shape, and cell volume before downstream calculations."
        ),
    }
    return [relaxation, *steps]


def _incar_tag_value(text: str, tag: str) -> str:
    match = re.search(rf"(?mi)^\s*{re.escape(tag)}\s*=\s*([^!#;\n]+)", text)
    return match.group(1).strip() if match else ""


def _remove_incar_tags(text: str, tags: set[str]) -> str:
    pattern = re.compile(
        rf"(?mi)^\s*(?:{'|'.join(re.escape(tag) for tag in sorted(tags))})\s*=.*(?:\n|$)"
    )
    return pattern.sub("", text).rstrip() + "\n"


def _incar_assignments(text: str) -> Dict[str, str]:
    return {
        match.group(1).upper(): match.group(2).strip()
        for match in re.finditer(r"(?mi)^\s*([A-Z][A-Z0-9_]*)\s*=\s*([^!#;\n]+)", text)
    }


def _derive_downstream_incar(base_text: str, generated_text: str, task: str) -> str:
    """Derive a downstream INCAR from relaxation settings, not from scratch."""
    kind = _vasp_task_kind(task)
    text = _remove_incar_tags(
        base_text, {"IBRION", "ISIF", "NSW", "POTIM", "EDIFFG", "ICHARG"}
    )
    allowed = {
        "scf": {"ALGO", "EDIFF", "NELM", "ISMEAR", "SIGMA", "LCHARG", "LWAVE"},
        "bands": {"NBANDS", "LORBIT", "LMAXMIX", "ISMEAR", "SIGMA", "EFERMI"},
        "dos": {"NEDOS", "EMIN", "EMAX", "LORBIT", "LMAXMIX", "ISMEAR", "SIGMA"},
        "nscf": {"NBANDS", "LORBIT", "LMAXMIX", "ISMEAR", "SIGMA"},
    }.get(kind, set())
    assignments = _incar_assignments(generated_text)
    for tag in sorted(allowed):
        if tag in assignments:
            text = _set_or_add_incar_tag(text, tag, assignments[tag])
    return text.rstrip() + "\n"


def _preferred_vasp_poscar(query: str, material_info: Dict[str, Any]) -> str:
    """Serialize the MP primitive cell unless a conventional cell is requested."""
    user_candidates = material_info.get("user_structure") or []
    if user_candidates:
        structure = user_candidates[0] if isinstance(user_candidates, list) else user_candidates
        try:
            if isinstance(structure, dict):
                from pymatgen.core import Structure
                structure = Structure.from_dict(structure)
            return str(structure.to(fmt="poscar")).rstrip() + "\n"
        except Exception:
            return ""
    conventional_requested = bool(
        re.search(r"\b(?:conventional|standard\s+conventional)\s+cell\b", query or "", re.I)
    )
    key = "conventional_structure" if conventional_requested else "primitive_structure"
    candidates = material_info.get(key) or []
    if not candidates:
        return ""
    structure = candidates[0] if isinstance(candidates, list) else candidates
    try:
        if isinstance(structure, dict):
            from pymatgen.core import Structure
            structure = Structure.from_dict(structure)
        if hasattr(structure, "to"):
            return str(structure.to(fmt="poscar")).rstrip() + "\n"
    except Exception:
        return ""
    return ""


def _vasp_material_info_from_user_structure(path: str, run_dir: Path) -> Dict[str, Any]:
    """Load a user-selected structure for the shared QE/VASP plan-review UI."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"User structure file does not exist: {source}")
    try:
        from pymatgen.core import Structure
        try:
            structure = Structure.from_file(str(source))
        except Exception as general_error:
            if source.suffix.lower() not in {".in", ".pwi", ".pw"}:
                raise general_error
            from tool.structural_analysis import _load_structure
            structure = _load_structure(source)
    except Exception as exc:
        raise ValueError(
            f"Could not read user structure {source}. Use CIF, POSCAR/CONTCAR, "
            f"pymatgen JSON/YAML, or CSSR. Parser error: {exc}"
        ) from exc
    if len(structure) < 1 or structure.lattice.volume <= 0:
        raise ValueError(f"User structure is empty or has an invalid cell: {source}")
    stored = run_dir / "structure_user_supplied.cif"
    stored.write_text(structure.to(fmt="cif"), encoding="utf-8")
    provenance = {
        "source": "user_file",
        "original_path": str(source),
        "stored_cif": str(stored),
        "formula": structure.composition.reduced_formula,
        "sites": len(structure),
    }
    (run_dir / "structure_source.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "user_structure": [structure],
        "initial_structures": [structure.to(fmt="cif")],
        "primitive_structure": [],
        "conventional_structure": [],
        "material_ids": [],
        "summary": provenance,
    }


def _vasp_template_resources(path: str) -> tuple[int, int]:
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8") if path else ""
    except OSError:
        text = ""
    nodes_match = re.search(r"(?mi)^\s*#SBATCH\s+--nodes(?:=|\s+)(\d+)", text)
    cores_match = re.search(
        r"(?mi)^\s*#SBATCH\s+--(?:n?tasks-per-node)(?:=|\s+)(\d+)", text
    )
    return (
        int(nodes_match.group(1)) if nodes_match else 1,
        int(cores_match.group(1)) if cores_match else 1,
    )


def _enforce_vasp_workflow_incar(
    path: Path, task: str, *, needs_chgcar: bool = False
) -> None:
    """Apply non-negotiable workflow tags after model generation."""
    text = path.read_text(encoding="utf-8")
    kind = _vasp_task_kind(task)
    if kind == "vc-relax":
        for tag, value in (("IBRION", "2"), ("ISIF", "3")):
            text = _set_or_add_incar_tag(text, tag, value)
        try:
            nsw = int(float(_incar_tag_value(text, "NSW") or "0"))
        except ValueError:
            nsw = 0
        if nsw <= 0:
            text = _set_or_add_incar_tag(text, "NSW", "100")
    elif kind == "relax":
        for tag, value in (("IBRION", "2"), ("ISIF", "2")):
            text = _set_or_add_incar_tag(text, tag, value)
        try:
            nsw = int(float(_incar_tag_value(text, "NSW") or "0"))
        except ValueError:
            nsw = 0
        if nsw <= 0:
            text = _set_or_add_incar_tag(text, "NSW", "100")
    elif kind == "scf":
        text = _set_or_add_incar_tag(text, "IBRION", "-1")
        text = _set_or_add_incar_tag(text, "NSW", "0")
        if needs_chgcar:
            text = _set_or_add_incar_tag(text, "LCHARG", ".TRUE.")
    elif kind == "bands":
        for tag, value in (
            ("IBRION", "-1"), ("NSW", "0"), ("ICHARG", "11"),
            ("LCHARG", ".FALSE."), ("LWAVE", ".FALSE."),
        ):
            text = _set_or_add_incar_tag(text, tag, value)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _assemble_potcar(
    *,
    poscar_path: Path,
    incar_path: Path,
    destination: Path,
    potcar_root: str,
    functional: str,
    species: Optional[List[str]] = None,
) -> List[str]:
    root = Path(potcar_root).expanduser()
    if not root.exists():
        raise FileNotFoundError(
            f"VASP POTCAR root does not exist: {root}. Set CLUSTER_AGENT_VASP_POTCAR_ROOT."
        )
    species = species or _species_from_poscar(poscar_path.read_text(encoding="utf-8"))
    if not species:
        raise ValueError(f"Could not read species order from {poscar_path}.")

    potcar_parts: List[str] = []
    source_paths: List[str] = []
    for element in species:
        src = _find_potcar_for_species(root, element, functional)
        potcar_parts.append(src.read_text(encoding="utf-8", errors="replace").rstrip())
        source_paths.append(str(src))
    potcar_text = "\n".join(potcar_parts).rstrip() + "\n"
    destination.write_text(potcar_text, encoding="utf-8")

    enmax_values = _extract_enmax(potcar_text)
    if enmax_values:
        recommended = int(math.ceil(max(enmax_values) / 10.0) * 10)
        incar_text = incar_path.read_text(encoding="utf-8")
        match = re.search(r"(?mi)^\s*ENCUT\s*=\s*([0-9.]+)", incar_text)
        current = float(match.group(1)) if match else 0.0
        if current < recommended:
            incar_path.write_text(
                _set_or_add_incar_tag(incar_text, "ENCUT", str(recommended)).rstrip() + "\n",
                encoding="utf-8",
            )
    return source_paths


def _functional_root_names(functional: str) -> List[str]:
    functional = functional.upper()
    return {
        "PBE": ["potpaw_PBE", "PBE", "pbe"],
        "PBESOL": ["potpaw_PBEsol", "PBEsol", "pbesol", "PBESOL"],
        "LDA": ["potpaw", "LDA", "lda"],
        "PW91": ["potpaw_GGA", "PW91", "pw91"],
    }.get(functional, [functional, functional.lower()])


def _write_remote_potcar_assembler(
    *,
    destination: Path,
    species: List[str],
    potcar_root: str,
    functional: str,
) -> str:
    root_names = " ".join(shlex.quote(name) for name in _functional_root_names(functional))
    species_names = " ".join(shlex.quote(name) for name in species)
    script = f"""#!/bin/bash
set -euo pipefail

POTCAR_ROOT={shlex.quote(potcar_root)}
FUNCTIONAL={shlex.quote(functional)}
SPECIES=({species_names})
ROOT_NAMES=({root_names})

rm -f POTCAR
for element in "${{SPECIES[@]}}"; do
  found=""
  BASES=("$POTCAR_ROOT")
  for root_name in "${{ROOT_NAMES[@]}}"; do
    BASES+=("$POTCAR_ROOT/$root_name")
  done
  for base in "${{BASES[@]}}"; do
    for variant in "$element" "${{element}}_pv" "${{element}}_sv" "${{element}}_GW"; do
      candidate="$base/$variant/POTCAR"
      if [ -f "$candidate" ]; then
        found="$candidate"
        break 2
      fi
    done
    for candidate in "$base"/"$element"*/POTCAR; do
      if [ -f "$candidate" ]; then
        found="$candidate"
        break 2
      fi
    done
  done
  if [ -z "$found" ]; then
    echo "Could not find POTCAR for $element under $POTCAR_ROOT using $FUNCTIONAL" >&2
    exit 1
  fi
  echo "$found" >> POTCAR.sources
  cat "$found" >> POTCAR
done
"""
    destination.write_text(script, encoding="utf-8")
    destination.chmod(0o755)
    return str(destination)


def _validation_errors(input_set: VASPInputSet) -> List[str]:
    errors: List[str] = []
    required = ["POSCAR", "INCAR", "KPOINTS"]
    if input_set.potcar_mode == "local":
        required.append("POTCAR")
    else:
        required.append("assemble_potcar.sh")
    for name in required:
        path = input_set.directory / name
        if not path.exists() or path.stat().st_size == 0:
            errors.append(f"{name} is missing or empty")
    poscar = input_set.directory / "POSCAR"
    if (
        poscar.exists()
        and not _is_relaxed_poscar_placeholder(poscar)
        and not _species_from_poscar(poscar.read_text(encoding="utf-8"))
    ):
        errors.append("POSCAR does not contain a readable species/count block")
    incar = input_set.directory / "INCAR"
    if incar.exists():
        incar_text = incar.read_text(encoding="utf-8")
        for bad in ("&control", "&system", "ecutwfc", "ATOMIC_SPECIES", "K_POINTS"):
            if re.search(rf"(?i){re.escape(bad)}", incar_text):
                errors.append(f"INCAR appears to contain Quantum ESPRESSO syntax: {bad}")
        if not re.search(r"(?mi)^\s*ENCUT\s*=", incar_text):
            errors.append("INCAR is missing ENCUT")
    return errors


def _approve_vasp_inputs_popup(plan: str, input_sets: List[VASPInputSet]) -> bool:
    flat_files: List[Path] = []
    for item in input_sets:
        flat_files.extend(item.directory / name for name in ("POSCAR", "INCAR", "KPOINTS"))
        if item.potcar_mode == "remote":
            flat_files.append(item.directory / "assemble_potcar.sh")

    print("\n[approval] All VASP inputs are ready.")
    print("[approval] Opening the TritonDFT approval window; execution is paused until approval.")
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk

        root = tk.Tk()
        root.title("TritonDFT VASP plan and input approval")
        root.geometry("1100x760")
        root.update_idletasks()
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.focus_force()

        def release_topmost() -> None:
            try:
                root.attributes("-topmost", False)
                root.lift()
                root.focus_force()
            except tk.TclError:
                pass

        root.after(750, release_topmost)
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        plan_text = tk.Text(notebook, wrap="word", font=("Menlo", 12))
        plan_text.insert("1.0", plan)
        plan_text.configure(state="disabled")
        notebook.add(plan_text, text="Complete plan")

        editors: Dict[Path, Any] = {}
        for path in flat_files:
            editor = tk.Text(notebook, wrap="none", undo=True, font=("Menlo", 11))
            editor.insert("1.0", path.read_text(encoding="utf-8"))
            notebook.add(editor, text=f"{path.parent.name}/{path.name}")
            editors[path] = editor

        validation_text = tk.Text(notebook, wrap="word", font=("Menlo", 11))
        validation_text.configure(state="disabled")
        notebook.add(validation_text, text="Validation")

        decision = {"approved": False}

        def save_edits() -> None:
            for path, editor in editors.items():
                path.write_text(editor.get("1.0", "end-1c").rstrip() + "\n", encoding="utf-8")

        def refresh_validation() -> List[str]:
            save_edits()
            messages: List[str] = []
            for item in input_sets:
                errors = _validation_errors(item)
                if errors:
                    messages.append(f"{item.directory.name}:\n  - " + "\n  - ".join(errors))
            report = "\n\n".join(messages) if messages else "Basic VASP input validation passed."
            validation_text.configure(state="normal")
            validation_text.delete("1.0", "end")
            validation_text.insert("1.0", report)
            validation_text.configure(state="disabled")
            return messages

        def close_window() -> None:
            try:
                root.withdraw()
                root.update_idletasks()
                root.destroy()
            except tk.TclError:
                pass

        def approve() -> None:
            messages = refresh_validation()
            if messages:
                notebook.select(validation_text)
                messagebox.showerror("Input validation failed", "Fix the listed issues before approval.")
                return
            decision["approved"] = True
            root.after_idle(close_window)

        def cancel() -> None:
            if messagebox.askyesno("Cancel workflow", "Cancel without submitting any cluster jobs?"):
                root.after_idle(close_window)

        controls = ttk.Frame(root)
        controls.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(controls, text="Review or edit generated VASP input files before cluster submission.").pack(side="left")
        ttk.Button(controls, text="Cancel", command=cancel).pack(side="right", padx=(8, 0))
        ttk.Button(controls, text="Approve & Run", command=approve).pack(side="right")
        ttk.Button(controls, text="Validate", command=refresh_validation).pack(side="right", padx=(0, 8))
        root.protocol("WM_DELETE_WINDOW", cancel)
        refresh_validation()
        root.mainloop()
        return decision["approved"]
    except Exception as exc:
        print(f"[approval] GUI unavailable ({exc}). Falling back to terminal approval.")
        print("\n" + plan)
        for path in flat_files:
            print(f"  - {path}")
        while True:
            answer = input("Type 'approve' to run, 'edit' after editing files, or 'cancel': ").strip().lower()
            if answer in {"approve", "yes", "y"}:
                return True
            if answer in {"cancel", "no", "n"}:
                return False
            if answer == "edit":
                print("Edit the files in your editor, then return here to approve or cancel.")


class VASPAgent:
    def __init__(
        self,
        *,
        model: str,
        backend: str,
        work_dir: str,
        potcar_root: str,
        vasp_command: str = "",
        functional: str = "",
        slurm_template_path: str = "",
        max_new_tokens: int = 4096,
        verbose: bool = False,
        need_query_info: bool = False,
        output_log: bool = False,
        output_log_file: str = "remote_cluster_agent.log",
    ):
        self.model = model
        self.backend = backend
        self.work_dir_root = Path(work_dir).expanduser().resolve()
        self.work_dir_root.mkdir(parents=True, exist_ok=True)
        self.work_dir = self.work_dir_root
        self.potcar_root = potcar_root
        self.default_vasp_command = vasp_command.strip()
        self.default_functional = functional.strip().upper()
        self.vasp_command = ""
        self.functional = ""
        self.slurm_template_path = slurm_template_path
        self.max_new_tokens = max_new_tokens
        self.verbose = verbose
        self.need_query_info = need_query_info
        self.output_log = output_log
        self.output_log_file = output_log_file
        self.generator = UnifiedGenerator(
            backend=backend,
            model=model,
            default_max_new_tokens=max_new_tokens,
            temperature=0.0,
            top_p=1.0,
            seed=1234,
            verbose=verbose,
        )

    def _prepare_run_directory(
        self,
        query: str,
        *,
        run_id: int = 0,
        category: str = "unknown",
        task_type: str = "",
        material_name: str = "",
    ) -> Path:
        if query and (not material_name or not task_type):
            extracted = _extract_query_metadata(query)
            material_name = material_name or extracted["material_name"]
            task_type = task_type or extracted["task_type"]
        now = datetime.datetime.now()
        date_dir = self.work_dir_root / now.strftime("%Y-%m-%d")
        parts = [_sanitize_name(p) for p in (material_name, task_type) if p]
        if not parts:
            parts.append("vasp_run")
        parts.extend([now.strftime("%H%M%S"), uuid.uuid4().hex[:8]])
        self.work_dir = date_dir / "_".join(parts)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "run_id": run_id,
            "category": category,
            "query": query,
            "model": self.model,
            "dft_tool": "vasp",
            "created_at": now.isoformat(),
            "directory": str(self.work_dir),
            "potcar_root": self.potcar_root,
            "default_potcar_functional": self.default_functional,
            "default_vasp_command": self.default_vasp_command,
        }
        (self.work_dir / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return self.work_dir

    def info_query(self, query: str) -> Dict[str, Any]:
        if not self.need_query_info:
            return {}
        api_messages = get_prompt(prompt_type="api_call", query=query)
        api_out = self.generator(
            api_messages[0]["content"],
            max_new_tokens=self.max_new_tokens,
            return_full_text=False,
        )
        snippet = api_out[0]["generated_text"]
        info = fetch_material_info_from_api_snippet(snippet, limit=25, verbose=self.verbose)
        if self.output_log:
            output_to_log_file(self.work_dir_root, self.output_log_file, f"[vasp info_query] {info.get('material_ids', ['N/A'])[0]}")
        return info

    def plan(self, query: str) -> List[Dict[str, Any]]:
        text = _generate_nonempty_text(
            self.generator,
            _vasp_plan_prompt(query),
            max_new_tokens=self.max_new_tokens,
            attempts=3,
            verbose=self.verbose,
            purpose="vasp_plan",
        )
        data = _parse_json_object(text)
        steps = data.get("steps", [])
        if not isinstance(steps, list) or not steps:
            steps = [{"title": "Static VASP calculation", "task": "static", "why": "Default single-step VASP calculation."}]
        settings = data.get("settings", {})
        valid_steps = [step for step in steps if isinstance(step, dict)]
        valid_steps = _apply_default_vasp_relaxation(query, valid_steps)
        for step in valid_steps:
            step.setdefault("_workflow_settings", settings if isinstance(settings, dict) else {})
        return valid_steps

    def _select_run_settings(self, query: str, steps: List[Dict[str, Any]]) -> VASPRunSettings:
        settings: Dict[str, Any] = {}
        if steps and isinstance(steps[0].get("_workflow_settings"), dict):
            settings = steps[0].get("_workflow_settings", {})

        requested_functional = str(settings.get("potcar_functional") or "").strip()
        requested_command = str(settings.get("vasp_command") or "").strip()

        functional = (
            _infer_potcar_functional(query)
            or _normalize_potcar_functional(requested_functional)
            or _normalize_potcar_functional(self.default_functional)
            or "PBE"
        )
        command = (
            _infer_vasp_command(query, steps)
            or _normalize_vasp_command(requested_command)
            or _normalize_vasp_command(self.default_vasp_command)
            or "vasp_std"
        )
        reason = str(settings.get("reason") or "").strip()
        if not reason:
            reason = (
                f"Selected {functional} POTCARs and {command} from the user request, "
                "with configured values used only as fallbacks."
            )
        return VASPRunSettings(potcar_functional=functional, vasp_command=command, reason=reason)

    def prepare_workflow(
        self,
        query: str,
        *,
        run_id: int = 0,
        category: str = "unknown",
        task_type: str = "",
        material_name: str = "",
    ) -> Dict[str, Any]:
        """Plan a VASP workflow and collect structure data before input generation."""
        self._prepare_run_directory(
            query,
            run_id=run_id,
            category=category,
            task_type=task_type,
            material_name=material_name,
        )
        material_info = self.info_query(query)
        steps = self.plan(query)
        run_settings = self._select_run_settings(query, steps)
        self.functional = run_settings.potcar_functional
        self.vasp_command = run_settings.vasp_command
        self._update_run_meta(
            {
                "potcar_functional": self.functional,
                "vasp_command": self.vasp_command,
                "vasp_settings_reason": run_settings.reason,
            }
        )
        plan_text = self._plan_text(steps)
        (self.work_dir / "workflow_plan.txt").write_text(plan_text, encoding="utf-8")
        (self.work_dir / "workflow_plan.json").write_text(json.dumps(steps, indent=2) + "\n", encoding="utf-8")

        summary = material_info.get("summary") or {}
        material_id = (material_info.get("material_ids") or [""])[0]
        structure_status = (
            f"Materials Project {material_id}: {summary.get('formula', '?')}, "
            f"{summary.get('space_group', '?')}, {summary.get('n_sites_primitive', '?')} sites (primitive)."
            if material_id else "No Materials Project structure was retrieved."
        )
        return {
            "query": query,
            "material_info": material_info,
            "steps": steps,
            "run_settings": run_settings,
            "plan_text": plan_text,
            "materials_project_available": bool(material_info.get("primitive_structure")),
            "materials_project_status": structure_status,
        }

    def generate_inputs(
        self,
        query: str,
        *,
        run_id: int = 0,
        category: str = "unknown",
        task_type: str = "",
        material_name: str = "",
        prepared: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context = prepared or self.prepare_workflow(
            query,
            run_id=run_id,
            category=category,
            task_type=task_type,
            material_name=material_name,
        )
        material_info = context["material_info"]
        preferred_poscar = _preferred_vasp_poscar(query, material_info)
        steps = context["steps"]
        run_settings = context["run_settings"]
        self.functional = run_settings.potcar_functional
        self.vasp_command = run_settings.vasp_command
        plan_text = context["plan_text"]

        previous_context = ""
        input_sets: List[VASPInputSet] = []
        task_occurrences: Dict[str, int] = {}
        relaxation_species: Optional[List[str]] = None
        relaxation_seen = False
        relaxation_incar = ""
        for index, step in enumerate(steps, start=1):
            print(f"[vasp-agent] generating input set {index}/{len(steps)}: {step.get('title') or step.get('task')}")
            prompt = _vasp_input_prompt(
                query=query,
                step=step,
                step_index=index,
                total_steps=len(steps),
                material_info=material_info,
                previous_context=previous_context,
                potcar_root=self.potcar_root,
                functional=self.functional,
                vasp_command=self.vasp_command,
            )
            generated = _generate_nonempty_text(
                self.generator,
                prompt,
                max_new_tokens=max(self.max_new_tokens, 8192),
                attempts=3,
                verbose=self.verbose,
                purpose=f"vasp_input_{index}",
            )
            data = _parse_json_object(generated)
            task_name = str(step.get("task") or "vasp").strip()
            task_kind = _vasp_task_kind(task_name)
            occurrence = task_occurrences.get(task_name, 0) + 1
            task_occurrences[task_name] = occurrence
            step_dir = self.work_dir / _step_directory_name(task_name, occurrence, max_len=40)
            step_dir.mkdir(parents=True, exist_ok=True)
            files: List[str] = []
            for name in VASP_FILE_NAMES:
                content = str(data.get(name, "")).strip()
                if not content:
                    raise ValueError(f"VASP generation omitted {name} for step {index}.")
                path = step_dir / name
                path.write_text(content.rstrip() + "\n", encoding="utf-8")
                files.append(str(path))
            if index == 1 and preferred_poscar:
                (step_dir / "POSCAR").write_text(preferred_poscar, encoding="utf-8")
            generated_species = _species_from_poscar(
                (step_dir / "POSCAR").read_text(encoding="utf-8")
            )
            uses_relaxed_poscar = relaxation_seen and task_kind not in {"relax", "vc-relax"}
            if uses_relaxed_poscar:
                if not relaxation_species:
                    raise ValueError(
                        "Cannot create a downstream POSCAR placeholder without relaxation species."
                    )
                (step_dir / "POSCAR").write_text(
                    VASP_RELAXED_POSCAR_PLACEHOLDER, encoding="utf-8"
                )
                species = list(relaxation_species)
            else:
                species = generated_species
            if task_kind in {"relax", "vc-relax"}:
                if not species:
                    raise ValueError(
                        f"Relaxation POSCAR does not contain readable species for step {index}."
                    )
                relaxation_species = list(species)
                relaxation_seen = True
            if uses_relaxed_poscar:
                if not relaxation_incar:
                    raise ValueError(
                        "Cannot derive downstream INCAR without the relaxation INCAR."
                    )
                generated_incar = (step_dir / "INCAR").read_text(encoding="utf-8")
                (step_dir / "INCAR").write_text(
                    _derive_downstream_incar(relaxation_incar, generated_incar, task_name),
                    encoding="utf-8",
                )
            needs_chgcar = any(
                _vasp_task_kind(str(future.get("task") or "")) == "bands"
                for future in steps[index:]
                if isinstance(future, dict)
            )
            _enforce_vasp_workflow_incar(
                step_dir / "INCAR", task_name, needs_chgcar=needs_chgcar
            )
            potcar_root_path = Path(self.potcar_root).expanduser()
            if potcar_root_path.exists():
                potcar_mode = "local"
                potcar_sources = _assemble_potcar(
                    poscar_path=step_dir / "POSCAR",
                    incar_path=step_dir / "INCAR",
                    destination=step_dir / "POTCAR",
                    potcar_root=self.potcar_root,
                    functional=self.functional,
                    species=species,
                )
                (step_dir / "POTCAR.sources.json").write_text(
                    json.dumps(
                        {
                            "mode": "local",
                            "sources": potcar_sources,
                            "functional": self.functional,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                files.append(str(step_dir / "POTCAR"))
            else:
                potcar_mode = "remote"
                assemble_path = _write_remote_potcar_assembler(
                    destination=step_dir / "assemble_potcar.sh",
                    species=species,
                    potcar_root=self.potcar_root,
                    functional=self.functional,
                )
                (step_dir / "POTCAR.sources.json").write_text(
                    json.dumps(
                        {
                            "mode": "remote",
                            "remote_root": self.potcar_root,
                            "functional": self.functional,
                            "species": species,
                            "assembler": "assemble_potcar.sh",
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                files.append(assemble_path)
            if task_kind in {"relax", "vc-relax"}:
                relaxation_incar = (step_dir / "INCAR").read_text(encoding="utf-8")
            input_set = VASPInputSet(
                step_index=index,
                title=str(step.get("title") or f"VASP step {index}"),
                task=str(step.get("task") or "static"),
                directory=step_dir,
                files=files,
                species=species,
                output_path=str(step_dir / "vasp.out"),
                potcar_mode=potcar_mode,
            )
            errors = _validation_errors(input_set)
            if errors:
                raise ValueError(f"Generated VASP input set failed validation: {'; '.join(errors)}")
            input_sets.append(input_set)
            previous_context += (
                f"\nStep {index}: {input_set.title} ({input_set.task}); "
                f"species order {' '.join(species)}; directory {step_dir.name}."
            )

        manifest = {
            "query": query,
            "plan": steps,
            "vasp_settings": {
                "potcar_functional": self.functional,
                "vasp_command": self.vasp_command,
                "reason": run_settings.reason,
            },
            "input_sets": [
                {
                    "step": item.step_index,
                    "title": item.title,
                    "task": item.task,
                    "directory": str(item.directory),
                    "files": item.files,
                    "species": item.species,
                    "potcar_mode": item.potcar_mode,
                    "poscar_mode": (
                        "relaxed_contcar_placeholder"
                        if _is_relaxed_poscar_placeholder(item.directory / "POSCAR")
                        else "generated"
                    ),
                    "incar_source": (
                        "relaxation_incar_with_task_edits"
                        if _is_relaxed_poscar_placeholder(item.directory / "POSCAR")
                        else "generated"
                    ),
                    "output_path": item.output_path,
                }
                for item in input_sets
            ],
            "status": "awaiting_approval",
        }
        (self.work_dir / "approval_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return {
            "plan_text": plan_text,
            "plan": steps,
            "input_sets": input_sets,
            "manifest": manifest,
            "materials_project_available": context["materials_project_available"],
            "materials_project_status": context["materials_project_status"],
        }

    def _update_run_meta(self, values: Dict[str, Any]) -> None:
        path = self.work_dir / "run_meta.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}
        data.update(values)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _plan_text(steps: List[Dict[str, Any]]) -> str:
        lines = ["TritonDFT VASP execution plan", "============================="]
        for idx, step in enumerate(steps, start=1):
            lines.extend(
                [
                    "",
                    f"{idx}. {step.get('title') or step.get('task') or 'VASP step'}",
                    f"   Task: {step.get('task') or 'static'}",
                    f"   Why: {step.get('why') or 'This step contributes to the requested VASP workflow.'}",
                ]
            )
        lines.extend(
            [
                "",
                "Approval gate",
                "-------------",
                "The initial POSCAR plus all KPOINTS and INCAR files are generated before execution.",
                "After relaxation, downstream POSCAR files are protected placeholders replaced only by the verified CONTCAR.",
                "POTCAR is either assembled locally when available or assembled on the cluster before VASP runs.",
                "No cluster job is submitted until you approve the files.",
            ]
        )
        return "\n".join(lines) + "\n"


class RemoteClusterVASPAgent:
    def __init__(
        self,
        *,
        vasp_agent: VASPAgent,
        transport,
        approval_callback=None,
        plan_approval_callback=None,
        monitor_callback=None,
        parallel_np: int = 0,
        vasp_command: str = "",
        slurm_template_path: str = "",
    ):
        self.agent = vasp_agent
        self.transport = transport
        self.approval_callback = approval_callback or _approve_vasp_inputs_popup
        self.plan_approval_callback = plan_approval_callback
        self.monitor_callback = monitor_callback
        template_nodes, template_cores = _vasp_template_resources(
            slurm_template_path or vasp_agent.slurm_template_path
        )
        self.max_nodes = template_nodes
        self.parallel_np = max(1, parallel_np or template_cores)
        self.default_vasp_command = vasp_command.strip()
        self.slurm_template_path = slurm_template_path or vasp_agent.slurm_template_path

    def run(
        self,
        query: str,
        *,
        run_id: int = 0,
        category: str = "unknown",
        task_type: str = "",
        material_name: str = "",
    ) -> Dict[str, Any]:
        prepared = self.agent.prepare_workflow(
            query,
            run_id=run_id,
            category=category,
            task_type=task_type,
            material_name=material_name,
        )
        plan_text = prepared["plan_text"]
        self._active_query = query
        print("\n" + plan_text)

        manifest_path = self.agent.work_dir / "approval_manifest.json"
        if self.plan_approval_callback is not None:
            workflow_steps = [
                {
                    "id": index,
                    "tool": str(step.get("task") or "vasp"),
                    "problem": str(step.get("title") or step.get("task") or f"VASP step {index}"),
                    "why": str(step.get("why") or ""),
                }
                for index, step in enumerate(prepared["steps"], start=1)
            ]
            plan_decision = self.plan_approval_callback(
                plan_text,
                [],
                review_stage="plan",
                workflow_steps=workflow_steps,
                resource_defaults={"max_nodes": self.max_nodes, "cores_per_node": self.parallel_np},
                structure_defaults={
                    "materials_project_available": bool(prepared.get("materials_project_available")),
                    "materials_project_status": prepared.get("materials_project_status", ""),
                },
            )
            action = plan_decision.get("action", "cancel") if isinstance(plan_decision, dict) else (
                "approve" if plan_decision else "cancel"
            )
            if action != "approve":
                if action == "revise" and isinstance(plan_decision, dict):
                    revision = str(plan_decision.get("revision") or "").strip()
                    (self.agent.work_dir / "plan_revision.json").write_text(
                        json.dumps({
                            "query": query,
                            "comment": revision,
                            "superseded_run_dir": str(self.agent.work_dir),
                        }, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    return self.run(
                        query + "\n\nUSER-REQUESTED SCIENTIFIC/PLAN REVISION (mandatory):\n" + revision,
                        run_id=run_id,
                        category=category,
                        task_type=task_type,
                        material_name=material_name,
                    )
                return {
                    "status": "cancelled_before_input_generation",
                    "run_dir": str(self.agent.work_dir),
                }
            if isinstance(plan_decision, dict):
                resources = plan_decision.get("resources") or {}
                self.parallel_np = max(1, int(resources.get("cores_per_node") or self.parallel_np))
                self.max_nodes = max(1, int(resources.get("max_nodes") or self.max_nodes))
                structure = plan_decision.get("structure") or {}
                if structure.get("source") == "file":
                    prepared["material_info"] = _vasp_material_info_from_user_structure(
                        str(structure.get("path") or ""), self.agent.work_dir
                    )

        generated = self.agent.generate_inputs(query, prepared=prepared)
        input_sets: List[VASPInputSet] = generated["input_sets"]
        manifest = generated["manifest"]
        manifest["resources"] = {
            "max_nodes": self.max_nodes,
            "cores_per_node": self.parallel_np,
        }
        if not self.approval_callback(plan_text, input_sets):
            manifest["status"] = "cancelled"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            return {
                "status": "cancelled",
                "run_dir": str(self.agent.work_dir),
                "plan": generated["plan"],
                "input_sets": [str(item.directory) for item in input_sets],
            }

        manifest["status"] = "approved"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        input_sets = self._materialize_job_directories(input_sets)
        manifest["jobs"] = [
            {
                "step": item.step_index,
                "task": item.task,
                "directory": str(item.directory),
                "output_path": item.output_path,
                "status": "created",
            }
            for item in input_sets
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        self._write_slurm_scripts(input_sets)
        self._write_workflow_state(generated["plan"], input_sets, "approved")
        if self.monitor_callback is not None:
            self.monitor_callback(self.agent.work_dir)
        self.transport.ensure_connection()

        latest_contcar: Optional[Path] = None
        latest_scf_remote_dir = ""
        reference_potcar_hash = ""
        for item in input_sets:
            task_kind = _vasp_task_kind(item.task)
            poscar_path = item.directory / "POSCAR"
            if _is_relaxed_poscar_placeholder(poscar_path) and not (
                latest_contcar and latest_contcar.exists()
            ):
                raise RuntimeError(
                    f"Step {item.step_index} requires the relaxed CONTCAR, but no "
                    "completed relaxation structure is available."
                )
            if latest_contcar and latest_contcar.exists():
                shutil.copy2(latest_contcar, poscar_path)
                materialized_species = _species_from_poscar(
                    poscar_path.read_text(encoding="utf-8", errors="replace")
                )
                if materialized_species != item.species:
                    raise RuntimeError(
                        "Relaxed CONTCAR species/order does not match the approved POTCAR "
                        f"selection for step {item.step_index}."
                    )
                job_record_index = item.step_index - 1
                manifest["jobs"][job_record_index]["poscar_source"] = str(latest_contcar)
                print(f"[vasp-agent] using relaxed CONTCAR from the previous step as {item.directory.name}/POSCAR")
            if _is_relaxed_poscar_placeholder(poscar_path):
                raise RuntimeError(
                    f"Refusing to submit step {item.step_index} with an unresolved POSCAR placeholder."
                )

            if hasattr(self.transport, "remote_attempt_dir"):
                remote_dir = self.transport.remote_attempt_dir(
                    self.agent.work_dir, item.step_index, item.task, 1
                )
            else:
                remote_dir = (
                    f"{self.transport.remote_root}/{self.agent.work_dir.name}/steps/"
                    f"{item.step_index:02d}-{_sanitize_name(item.task).lower()}/attempt_001"
                )
            job_record = manifest["jobs"][item.step_index - 1]
            job_record["remote_directory"] = remote_dir
            job_record["status"] = "preparing"
            print(
                f"[cluster] preparing VASP step {item.step_index}: uploading inputs and "
                "assembling/verifying POTCAR. Slurm job ID is pending until sbatch succeeds."
            )
            self._write_workflow_state(
                generated["plan"], input_sets, "preparing",
                item.step_index, "preparing",
            )
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            try:
                upload_paths = [
                    str(path) for path in item.directory.iterdir() if path.is_file()
                ]
                if hasattr(self.transport, "upload_step_files"):
                    self.transport.upload_step_files(
                        item.directory, remote_dir, upload_paths
                    )
                else:
                    self.transport.upload_directory(item.directory, remote_dir)
                if hasattr(self.transport, "remote_files_ok"):
                    missing = self.transport.remote_files_ok(remote_dir, upload_paths)
                    if missing:
                        raise RuntimeError(
                            "VASP job upload verification failed:\n" + "\n".join(missing)
                        )
                if item.potcar_mode == "remote":
                    job_record["status"] = "assembling_potcar"
                    manifest_path.write_text(
                        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                    )
                    if not hasattr(self.transport, "assemble_remote_vasp_potcar"):
                        raise RuntimeError(
                            "The cluster transport cannot assemble a remote VASP POTCAR."
                        )
                    self.transport.assemble_remote_vasp_potcar(remote_dir)
                    if hasattr(self.transport, "remote_files_ok"):
                        missing = self.transport.remote_files_ok(remote_dir, ["POTCAR"])
                        if missing:
                            raise RuntimeError(
                                "Remote POTCAR assembly verification failed:\n"
                                + "\n".join(missing)
                            )
                    job_record["potcar_status"] = "assembled_and_verified"
                if hasattr(self.transport, "remote_files_ok"):
                    missing = self.transport.remote_files_ok(remote_dir, ["POTCAR"])
                    if missing:
                        raise RuntimeError(
                            "VASP POTCAR is missing before submission:\n" + "\n".join(missing)
                        )
                if hasattr(self.transport, "remote_file_sha256"):
                    potcar_hash = self.transport.remote_file_sha256(remote_dir, "POTCAR")
                    if reference_potcar_hash and potcar_hash != reference_potcar_hash:
                        raise RuntimeError(
                            "POTCAR changed between VASP workflow steps; submission blocked."
                        )
                    reference_potcar_hash = reference_potcar_hash or potcar_hash
                    job_record["potcar_sha256"] = potcar_hash
                if task_kind == "bands":
                    if not latest_scf_remote_dir:
                        raise RuntimeError(
                            "Band calculation requires a completed SCF CHGCAR, but no SCF step completed."
                        )
                    if not hasattr(self.transport, "copy_remote_artifacts"):
                        raise RuntimeError(
                            "The cluster transport cannot stage CHGCAR for the band calculation."
                        )
                    self.transport.copy_remote_artifacts(
                        latest_scf_remote_dir, remote_dir, ["CHGCAR"]
                    )
                    if hasattr(self.transport, "remote_files_ok"):
                        missing = self.transport.remote_files_ok(remote_dir, ["CHGCAR"])
                        if missing:
                            raise RuntimeError(
                                "Band calculation requires a non-empty SCF CHGCAR:\n"
                                + "\n".join(missing)
                            )
                    job_record["chgcar_source"] = latest_scf_remote_dir + "/CHGCAR"
                print(
                    f"[cluster] submitting VASP step {item.step_index} with "
                    f"{Path(item.slurm_path).name}; waiting for Slurm job ID."
                )
                job = self.transport.submit(remote_dir, Path(item.slurm_path).name)
                job_record["job_id"] = job.job_id
                job_record["status"] = "submitted"
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                self._write_workflow_state(
                    generated["plan"], input_sets, "running",
                    item.step_index, "submitted", job.job_id,
                )
                print(f"[cluster] VASP step {item.step_index} submitted; Slurm job ID: {job.job_id}")
                print(f"[vasp-agent] sbatch response: {job.submit_output}")
                self.transport.wait_for_job(job)
                required_outputs = ["vasp.out"]
                if task_kind == "vc-relax" or task_kind == "relax":
                    required_outputs.append("CONTCAR")
                if task_kind == "scf" and any(
                    _vasp_task_kind(later.task) == "bands"
                    for later in input_sets[item.step_index:]
                ):
                    required_outputs.append("CHGCAR")
                if hasattr(self.transport, "remote_files_ok"):
                    missing = self.transport.remote_files_ok(remote_dir, required_outputs)
                    if missing:
                        raise RuntimeError(
                            "VASP job completed without required outputs:\n" + "\n".join(missing)
                        )
                self.transport.fetch_directory(remote_dir, item.directory)
                job_record["status"] = "completed"
                self._write_workflow_state(generated["plan"], input_sets, "running", item.step_index, "completed", job.job_id)
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            except Exception as exc:
                job_record["status"] = "failed"
                job_record["error"] = str(exc)
                manifest["status"] = "failed"
                self._write_workflow_state(generated["plan"], input_sets, "failed", item.step_index, "failed")
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                raise
            if task_kind == "scf":
                latest_scf_remote_dir = remote_dir
            contcar = item.directory / "CONTCAR"
            if task_kind in {"relax", "vc-relax"} and contcar.exists() and contcar.stat().st_size > 0:
                latest_contcar = contcar
                # Any charge density produced before this geometry is stale.
                latest_scf_remote_dir = ""

        analysis = self._summarize_outputs(query, input_sets)
        (self.agent.work_dir / "analysis.json").write_text(
            json.dumps({"query": query, "analysis": analysis}, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["status"] = "completed"
        self._write_workflow_state(generated["plan"], input_sets, "completed")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return {
            "status": "success",
            "run_dir": str(self.agent.work_dir),
            "input_sets": [
                {
                    "step": item.step_index,
                    "directory": str(item.directory),
                    "output_path": item.output_path,
                    "slurm_path": item.slurm_path,
                }
                for item in input_sets
            ],
            "analysis": analysis,
        }

    def _write_workflow_state(
        self,
        plan: List[Dict[str, Any]],
        input_sets: List[VASPInputSet],
        workflow_status: str,
        active_step: int = 0,
        step_status: str = "",
        job_id: str = "",
    ) -> None:
        path = self.agent.work_dir / "workflow_state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            state = {}
        old_steps = {int(step.get("id", 0)): step for step in state.get("steps", [])}
        steps = []
        for item, planned in zip(input_sets, plan):
            old = old_steps.get(item.step_index, {})
            status = old.get("status", "pending")
            if item.step_index == active_step and step_status:
                status = step_status
            jobs = list(old.get("job_ids", []))
            if item.step_index == active_step and job_id and job_id not in jobs:
                jobs.append(job_id)
            steps.append({
                "id": item.step_index,
                "problem": item.title,
                "tool": item.task,
                "branch": item.task,
                "status": status,
                "attempts": 1 if status in {"running", "submitted", "completed", "failed"} else 0,
                "job_ids": jobs,
            })
        payload = {
            "version": 1,
            "query": state.get("query") or getattr(self, "_active_query", ""),
            "run_dir": str(self.agent.work_dir),
            "status": workflow_status,
            "plan": plan,
            "packages": [],
            "steps": steps,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _materialize_job_directories(
        self, input_sets: List[VASPInputSet]
    ) -> List[VASPInputSet]:
        """Copy approved VASP inputs into immutable QE-style job directories."""
        jobs: List[VASPInputSet] = []
        for item in input_sets:
            task_name = _sanitize_name(item.task).lower() or "vasp"
            job_dir = (
                self.agent.work_dir
                / "attempts"
                / f"{item.step_index:02d}-{task_name}"
                / "attempt_001"
            )
            job_dir.mkdir(parents=True, exist_ok=False)
            for source in item.directory.iterdir():
                if source.is_file():
                    shutil.copy2(source, job_dir / source.name)
            files = [
                str(job_dir / Path(path).name)
                for path in item.files
                if (job_dir / Path(path).name).is_file()
            ]
            jobs.append(
                VASPInputSet(
                    step_index=item.step_index,
                    title=item.title,
                    task=item.task,
                    directory=job_dir,
                    files=files,
                    species=list(item.species),
                    output_path=str(job_dir / "vasp.out"),
                    potcar_mode=item.potcar_mode,
                )
            )
        return jobs

    def _write_slurm_scripts(self, input_sets: List[VASPInputSet]) -> None:
        for item in input_sets:
            command = self._vasp_command()
            command_line = f"{command} > $OUTPUT"
            script = render_slurm_script(
                exec_path=self._vasp_executable_name(),
                input_path="POSCAR",
                output_path="vasp.out",
                command_line=command_line,
                nodes=self.max_nodes,
                tasks_per_node=self.parallel_np,
                work_dir=".",
                time_limit="01:00:00",
                template_path=self.slurm_template_path,
                preserve_template_launcher_options=True,
                preserve_template_resources=False,
                preserve_template_logs=True,
            )
            path = item.directory / "run_vasp.slurm"
            path.write_text(script.rstrip() + "\n", encoding="utf-8")
            item.slurm_path = str(path)

    def _vasp_command(self) -> str:
        command = (self.agent.vasp_command or self.default_vasp_command).strip()
        if not command:
            command = "vasp_std"
        if re.search(r"\b(mpirun|mpiexec|srun)\b", command):
            return command
        return f"mpirun -np {self.parallel_np} $exe"

    def _vasp_executable_name(self) -> str:
        command = (self.agent.vasp_command or self.default_vasp_command).strip() or "vasp_std"
        if re.search(r"\b(mpirun|mpiexec|srun)\b", command):
            parts = shlex.split(command)
            for token in reversed(parts):
                if not token.startswith("-") and "=" not in token:
                    return token
            return "vasp_std"
        return command

    def _summarize_outputs(self, query: str, input_sets: List[VASPInputSet]) -> str:
        snippets: List[str] = []
        for item in input_sets:
            out_path = Path(item.output_path)
            if out_path.exists():
                text = out_path.read_text(encoding="utf-8", errors="replace")
                snippets.append(f"Step {item.step_index} {item.task} output tail:\n{text[-6000:]}")
            else:
                snippets.append(f"Step {item.step_index} {item.task}: output file missing at {out_path}")
        prompt = f"""Summarize the result of this VASP workflow for the user.

User request:
{query}

Output snippets:
{chr(10).join(snippets)}

Return a concise scientific summary. Mention if any output file is missing or incomplete.
"""
        try:
            text = _generate_nonempty_text(
                self.agent.generator,
                prompt,
                max_new_tokens=self.agent.max_new_tokens,
                attempts=2,
                verbose=self.agent.verbose,
                purpose="vasp_summary",
            )
            return text.strip()
        except Exception as exc:
            return f"VASP workflow completed, but automatic result summarization failed: {exc}"
