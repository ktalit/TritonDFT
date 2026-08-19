from __future__ import annotations

import json
import ast
import math
import re
from dataclasses import dataclass
from pathlib import Path


TEXT_SUFFIXES = {".in", ".out", ".err", ".log", ".txt", ".json", ".xml", ".dat", ".dos", ".gnu", ".band", ".cif", ".yaml", ".yml"}
SKIP_PARTS = {".git", "__pycache__", "pseudos"}
STOP_WORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "give", "how", "i", "in", "is", "it", "me", "of", "on", "or", "show", "the", "this", "to", "was", "what", "where", "which", "with"}
ALIASES = {
    "gap": ("band gap", "highest occupied", "lowest unoccupied", "homo", "lumo"),
    "fermi": ("fermi energy", "the fermi energy is"),
    "lattice": ("cell_parameters", "lattice parameter", "celldm"),
    "moment": ("magnetic moment", "magnetization", "magn="),
    "raman": ("raman", "raman tensor", "raman cross section"),
    "phonon": ("freq", "frequency", "cm-1", "thz"),
    "energy": ("total energy", "!    total energy", "fermi energy"),
    "distance": ("cell_parameters", "atomic_positions", "lattice parameter"),
    "axis": ("cell_parameters", "atomic_positions"),
    "alpha": ("cell_parameters",),
    "beta": ("cell_parameters",),
    "gamma": ("cell_parameters",),
    "converged": ("convergence has been achieved", "end of self-consistent"),
    "cutoff": ("ecutwfc", "ecutrho"),
    "cutoffs": ("ecutwfc", "ecutrho"),
    "k-point": ("k_points", "number of k points", "monkhorst"),
    "kpoints": ("k_points", "number of k points", "monkhorst"),
    "functional": ("input_dft", "exchange-correlation", "xc functional"),
    "pseudopotential": ("atomic_species", "pseudo_dir", "pseudopotential"),
    "pseudopotentials": ("atomic_species", "pseudo_dir", "pseudopotential"),
    "soc": ("lspinorb", "noncolin", "fully relativistic", "spin-orbit"),
    "spin": ("nspin", "noncolin", "magnetization", "lspinorb"),
    "hubbard": ("hubbard_u", "lda_plus_u", "hubbard"),
    "stress": ("total stress", "p=", "pressure"),
    "force": ("forces acting on atoms", "total force", "forc_conv_thr"),
    "forces": ("forces acting on atoms", "total force", "forc_conv_thr"),
    "summary": ("job done", "total energy", "fermi energy", "highest occupied", "freq", "total magnetization"),
    "summarize": ("job done", "total energy", "fermi energy", "highest occupied", "freq", "total magnetization"),
    "results": ("job done", "total energy", "fermi energy", "highest occupied", "freq", "total magnetization"),
}


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    path: Path
    relative_path: str
    line_number: int
    line: str
    context: tuple[str, ...]
    score: float

    def prompt_text(self) -> str:
        return f"[{self.evidence_id}] FILE: {self.relative_path}\nLINE: {self.line_number}\nEXACT LINE: {self.line}\nCONTEXT:\n" + "\n".join(self.context)


def _query_terms(question: str) -> tuple[set[str], set[str]]:
    words = {word for word in re.findall(r"[a-z0-9_+.-]+", question.lower()) if len(word) > 1 and word not in STOP_WORDS}
    phrases = {phrase for word in words for phrase in ALIASES.get(word, ())}
    if "band" in words and "gap" in words:
        phrases.add("band gap")
    return words, phrases


def _completed_attempt_dirs(run_dir: Path) -> set[Path]:
    try:
        state = json.loads((run_dir / "workflow_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    completed = set()
    for step in state.get("steps", []):
        for attempt in step.get("attempt_history", []):
            if str(attempt.get("status", "")).lower() == "completed" and attempt.get("local_dir"):
                completed.add(Path(attempt["local_dir"]).expanduser().resolve())
    return completed


def workflow_inventory(run_dir: Path) -> dict:
    """Small, structured workflow map supplied to the answer model."""
    run_dir = run_dir.resolve()
    try:
        state = json.loads((run_dir / "workflow_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        state = {}
    steps = [{
        "id": step.get("id"), "tool": step.get("tool"), "branch": step.get("branch"),
        "status": step.get("status"), "attempts": step.get("attempts", 0),
        "problem": step.get("problem", ""),
    } for step in state.get("steps", [])]
    return {
        "run_dir": str(run_dir), "workflow_status": state.get("status", "unknown"),
        "original_request": state.get("query", ""), "steps": steps,
        "source_policy": "Prefer completed immutable attempts; generated/approved inputs are not results.",
    }


def workflow_file_manifest(run_dir: Path, *, include_failed: bool = False) -> list[str]:
    """Return local relative paths only; file contents are not included."""
    return [str(path.relative_to(run_dir.resolve()))
            for path in _candidate_files(run_dir.resolve(), include_failed=include_failed)]


def retrieval_plan_prompt(question: str, inventory: dict, manifest: list[str]) -> str:
    return f"""Plan local file retrieval for a question about a DFT workflow. You cannot read files directly.
Choose search phrases that TritonDFT should look for verbatim or approximately inside the files. Translate scientific requests into likely Quantum ESPRESSO/VASP output and input terms. For derived quantities, retrieve every required operand. Do not answer the question.
Return only JSON: {{"search_queries":["phrase", "phrase"], "reason":"brief"}}. Use 1 to 8 concise searches.

WORKFLOW:
{json.dumps(inventory, indent=2)}

AVAILABLE FILES:
{json.dumps(manifest[:500], indent=2)}

QUESTION:
{question}
"""


def parse_retrieval_plan(raw: str) -> list[str]:
    match = re.search(r"\{.*\}", str(raw), re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except (TypeError, ValueError):
        return []
    queries = []
    for value in payload.get("search_queries", []):
        query = " ".join(str(value).split()).strip()
        if query and query not in queries:
            queries.append(query)
    return queries[:8]


def merge_evidence(groups: list[list[Evidence]], limit: int = 40) -> list[Evidence]:
    """Deduplicate independently ranked searches and assign stable citation IDs."""
    unique: dict[tuple[str, int, str], Evidence] = {}
    for group in groups:
        for item in group:
            key = (str(item.path), item.line_number, item.line)
            previous = unique.get(key)
            if previous is None or item.score > previous.score:
                unique[key] = item
    ranked = sorted(unique.values(), key=lambda item: (-item.score, item.relative_path, item.line_number))[:limit]
    return [Evidence(f"E{index}", item.path, item.relative_path, item.line_number,
                     item.line, item.context, item.score)
            for index, item in enumerate(ranked, 1)]


def _candidate_files(run_dir: Path, *, include_failed: bool = False):
    completed_dirs = _completed_attempt_dirs(run_dir)
    has_attempts = (run_dir / "attempts").is_dir()
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run_dir)
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & SKIP_PARTS or any(part.endswith(".save") for part in lowered_parts):
            continue
        if "probe" in path.stem.lower():
            continue
        if has_attempts and lowered_parts & {"approved_inputs", "materialized_inputs"}:
            continue
        if "attempts" in lowered_parts and completed_dirs and not include_failed:
            resolved = path.resolve()
            if not any(directory == resolved.parent or directory in resolved.parents for directory in completed_dirs):
                continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "CRASH":
            continue
        try:
            if path.stat().st_size > 12_000_000:
                continue
        except OSError:
            continue
        yield path


def search_workflow_evidence(
    run_dir: Path, question: str, limit: int = 24, *, include_failed: bool = False
) -> list[Evidence]:
    """Return ranked, line-addressable evidence from files under run_dir only."""
    run_dir = run_dir.resolve()
    words, phrases = _query_terms(question)
    if not words and not phrases:
        return []
    matches = []
    for path in _candidate_files(run_dir, include_failed=include_failed):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        relative_lower = str(path.relative_to(run_dir)).lower()
        structural_question = bool(words & {"distance", "axis", "alpha", "beta", "gamma", "angle", "lattice", "structure"})
        structural_indices: set[int] = set()
        if structural_question:
            for header_index, header in enumerate(lines):
                header_lower = header.lower()
                if "cell_parameters" in header_lower:
                    structural_indices.update(range(header_index, min(len(lines), header_index + 4)))
                if "atomic_positions" in header_lower:
                    structural_indices.update(range(header_index, min(len(lines), header_index + 80)))
        for index, line in enumerate(lines):
            lowered = line.lower()
            word_hits = sum(word in lowered for word in words)
            phrase_hits = sum(phrase in lowered for phrase in phrases)
            structural_hit = index in structural_indices
            # Within ATOMIC_POSITIONS, retain only the header and species that
            # occur in the question; otherwise large structures swamp ranking.
            if structural_hit and not ("atomic_positions" in lowered or "cell_parameters" in lowered):
                first = lowered.split(maxsplit=1)[0] if lowered.split() else ""
                if index not in structural_indices or (first and first not in words and not re.match(r"^[+-]?\d", first)):
                    structural_hit = index > 0 and "cell_parameters" in lines[index - 1].lower()
            if not word_hits and not phrase_hits and not structural_hit:
                continue
            path_hits = sum(word in relative_lower for word in words)
            source_priority = 0.0
            if path.name == "relaxed_structure.in":
                source_priority += 6.0
            elif "attempts" in {part.lower() for part in path.relative_to(run_dir).parts}:
                source_priority += 4.0
            elif path.suffix.lower() == ".out":
                source_priority += 2.0
            if "approved_inputs" in {part.lower() for part in path.relative_to(run_dir).parts}:
                source_priority -= 3.0
            score = word_hits + 2.5 * phrase_hits + 0.3 * path_hits + (2.0 if structural_hit else 0.0) + source_priority
            start, end = max(0, index - 3), min(len(lines), index + 4)
            context = tuple(f"{number + 1}: {lines[number]}" for number in range(start, end))
            matches.append((score, path, index + 1, line, context))
    matches.sort(key=lambda item: (-item[0], str(item[1]), item[2]))
    return [Evidence(f"E{number}", path, str(path.relative_to(run_dir)), line_number, line, context, score)
            for number, (score, path, line_number, line, context) in enumerate(matches[:limit], 1)]


def evidence_prompt(question: str, evidence: list[Evidence], inventory: dict | None = None) -> str:
    excerpts = "\n\n".join(item.prompt_text() for item in evidence)
    inventory_text = json.dumps(inventory or {}, indent=2)
    return f"""Answer a question about one DFT workflow using ONLY the supplied evidence. Do not use outside knowledge. Do not infer a numerical result that is absent. Distinguish directly reported results from derived values and interpretation. If evidence is insufficient, say so. Generated input settings are not completed calculation results. Prefer evidence from completed immutable attempts and never mix incompatible branches or superseded attempts.
When the requested value is not printed directly but can be obtained by straightforward mathematics from the evidence, you MUST derive it. Supply one arithmetic expression using only numeric constants and these permitted functions: sqrt, acos, asin, atan, cos, sin, tan, abs, min, max, degrees, radians. Use radians internally for trigonometry. For lattice angles use the vector dot-product definition. For periodic fractional-coordinate separations use the minimum-image difference when appropriate.
Return only valid JSON: {{"answer":"concise answer, mentioning that it is calculated when derived", "answer_type":"direct|derived|interpretation|insufficient", "evidence_ids":["E1"], "confidence":"high|medium|low", "derivation":"formula/explanation", "calculation":{{"expression":"numeric expression or empty", "unit":"eV|angstrom|degree|...", "description":"name of calculated quantity"}}}}
Every evidence ID must directly support the answer. Never invent paths or quotations; the application displays verified source lines.

WORKFLOW INVENTORY:\n{inventory_text}\n\nQUESTION:\n{question}\n\nEVIDENCE:\n{excerpts}\n"""


def parse_evidence_answer(raw: str, available_ids: set[str]) -> dict:
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fenced.group(1) if fenced else raw
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise ValueError("The model did not return the required JSON response.")
        payload = json.loads(match.group(0))
    answer = str(payload.get("answer", "")).strip()
    if not answer:
        raise ValueError("The model returned an empty answer.")
    calculation = payload.get("calculation", {})
    if not isinstance(calculation, dict):
        calculation = {}
    answer_type = str(payload.get("answer_type", "interpretation")).lower()
    if answer_type not in {"direct", "derived", "interpretation", "insufficient"}:
        answer_type = "interpretation"
    return {"answer": answer,
            "answer_type": answer_type,
            "evidence_ids": [str(item) for item in payload.get("evidence_ids", []) if str(item) in available_ids],
            "confidence": str(payload.get("confidence", "low")),
            "derivation": str(payload.get("derivation", "")).strip(),
            "calculation": {
                "expression": str(calculation.get("expression", "")).strip(),
                "unit": str(calculation.get("unit", "")).strip(),
                "description": str(calculation.get("description", "calculated value")).strip(),
            }}


_CALC_FUNCTIONS = {
    "sqrt": math.sqrt, "acos": math.acos, "asin": math.asin, "atan": math.atan,
    "cos": math.cos, "sin": math.sin, "tan": math.tan, "abs": abs,
    "min": min, "max": max, "degrees": math.degrees, "radians": math.radians,
}
_CALC_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
                ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
                ast.Pow: lambda a, b: a ** b}


def evaluate_calculation(expression: str) -> float:
    """Evaluate a small numeric expression without Python eval or arbitrary names."""
    if not expression or len(expression) > 600:
        raise ValueError("No valid calculation expression was supplied.")
    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in _CALC_BINOPS:
            return _CALC_BINOPS[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _CALC_FUNCTIONS and not node.keywords:
            return _CALC_FUNCTIONS[node.func.id](*(visit(argument) for argument in node.args))
        raise ValueError("The proposed calculation contains a disallowed operation.")

    result = float(visit(tree))
    if not math.isfinite(result):
        raise ValueError("The proposed calculation did not produce a finite result.")
    return result


def verify_evidence(item: Evidence) -> bool:
    try:
        lines = item.path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return 0 < item.line_number <= len(lines) and lines[item.line_number - 1] == item.line
