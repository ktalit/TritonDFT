import re, ast
from typing import Dict, Any, List, Optional


def _as_structure(x):
    """Coerce an MP API payload into a pymatgen Structure.

    With use_document_model=False the API returns raw dicts, and whether a
    nested structure is monty-decoded to a Structure or left as a plain dict
    varies across mp-api/emmet-core versions (e.g. 0.45.x decodes, 0.46.x does
    not). Normalize both so downstream code is version-independent.
    """
    from pymatgen.core import Structure

    if x is None or isinstance(x, Structure):
        return x
    if isinstance(x, dict):
        return Structure.from_dict(x)
    return x


# ---------- Helper extraction functions ----------

def _extract_str(snippet: str, key: str) -> Optional[str]:
    """Extract a string argument like key="value"."""
    m = re.search(rf"{key}\s*=\s*([\"'])(.*?)\1", snippet)
    return m.group(2) if m else None


def _extract_int(snippet: str, key: str) -> Optional[int]:
    """Extract an integer argument like key=123."""
    m = re.search(rf"{key}\s*=\s*(\d+)", snippet)
    return int(m.group(1)) if m else None


def _extract_list(snippet: str, key: str) -> Optional[List[Any]]:
    """Extract a Python-style list argument like key=["A","B"]."""
    m = re.search(rf"{key}\s*=\s*(\[[^\]]*\])", snippet, re.DOTALL)
    if not m:
        return None


def _normalized_symmetry_symbol(value: str) -> str:
    return re.sub(r"[\s_]", "", str(value or "")).lower()


def _filter_docs_by_requested_symmetry(
    docs: List[Dict[str, Any]], *, sg_symbol: Optional[str],
    sg_number: Optional[int], point_group: Optional[str], analyzer_cls,
) -> List[Dict[str, Any]]:
    """Apply phase constraints before thermodynamic ranking."""
    if not sg_symbol and sg_number is None and not point_group:
        return docs
    matched = []
    for doc in docs:
        structure = _as_structure(doc.get("structure"))
        if structure is None:
            continue
        try:
            analyzer = analyzer_cls(structure, symprec=1e-3, angle_tolerance=5)
            actual_symbol = analyzer.get_space_group_symbol()
            actual_number = analyzer.get_space_group_number()
            actual_point_group = analyzer.get_point_group_symbol()
        except Exception:
            continue
        if sg_symbol and _normalized_symmetry_symbol(actual_symbol) != _normalized_symmetry_symbol(sg_symbol):
            continue
        if sg_number is not None and int(actual_number) != int(sg_number):
            continue
        if point_group and _normalized_symmetry_symbol(actual_point_group) != _normalized_symmetry_symbol(point_group):
            continue
        matched.append(doc)
    return matched
    try:
        return ast.literal_eval(m.group(1))
    except Exception:
        return None


# ---------- Core function ----------

def fetch_material_info_from_api_snippet(snippet: str, limit: int = 25, verbose: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Parse an MP API query snippet (e.g. 'mpr.materials.summary.search(formula="BaTiO3", spacegroup_symbol="P4mm")'),
    execute the query to obtain material IDs, then fetch their corresponding initial structures.

    Returns:
        {
          "mp-12345": {
              "initial_structures": [Structure, ...],   # May be empty if not stored
              "relaxed_structure": Structure or None    # Provided as fallback
          },
          ...
        }
    """
    try:
        from mp_api.client import MPRester
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    except ImportError as exc:
        raise ImportError(
            "Materials Project lookup requires the optional packages `mp_api` "
            "and `pymatgen`. Install them, or run with CLUSTER_AGENT_NO_QUERY_INFO=true "
            "to generate inputs without querying Materials Project."
        ) from exc

    # Step 1. Extract material_ids if explicitly given.
    material_ids = _extract_list(snippet, "material_ids")

    # Step 2. Otherwise parse formula/elements/spacegroup fields.
    formula = _extract_str(snippet, "formula")
    elements = _extract_list(snippet, "elements")
    sg_symbol = _extract_str(snippet, "spacegroup_symbol")
    sg_number = _extract_int(snippet, "spacegroup_number")
    point_group = _extract_str(snippet, "point_group_symbol") or _extract_str(snippet, "point_group")
    chemsys = _extract_str(snippet, "chemsys")

    # Step 3. Query lightweight summary records once. Include stability in this
    # request so selection does not issue one API request per candidate.
    if not material_ids:
        with MPRester(use_document_model=False) as mpr:
            docs_iter = mpr.materials.summary.search(
                formula=formula,
                elements=elements,
                spacegroup_symbol=sg_symbol,
                spacegroup_number=sg_number,
                chemsys=chemsys,
                fields=["material_id", "structure", "energy_above_hull"],
            )
            docs = list(docs_iter)
    else:
        with MPRester(use_document_model=False) as mpr:
            docs_iter = mpr.materials.summary.search(
                material_ids=material_ids,
                fields=["material_id", "structure", "energy_above_hull"],
            )
            docs = list(docs_iter)

    if not docs:
        return {}

    symmetry_docs = _filter_docs_by_requested_symmetry(
        docs, sg_symbol=sg_symbol, sg_number=sg_number, point_group=point_group,
        analyzer_cls=SpacegroupAnalyzer,
    )
    if (sg_symbol or sg_number is not None or point_group) and not symmetry_docs:
        requested = (
            sg_symbol if sg_symbol else
            f"space-group number {sg_number}" if sg_number is not None else
            f"point group {point_group}"
        )
        raise ValueError(
            f"Materials Project returned no structure matching the explicitly requested {requested}. "
            "TritonDFT will not substitute a different polymorph."
        )
    docs = symmetry_docs or docs

    # Within the requested phase, stable (zero-hull) entries naturally rank
    # first; if none is stable, the lowest positive hull energy wins. Use the
    # material id as a deterministic tie breaker when several polymorphs have
    # the same hull energy. A supplied space-group filter has already narrowed
    # the candidate set at the API level.
    ranked_docs = sorted(
        docs,
        key=lambda doc: (
            float("inf") if doc.get("energy_above_hull") is None
            else float(doc["energy_above_hull"]),
            str(doc.get("material_id", "")),
        ),
    )
    selected_doc = ranked_docs[0]
    min_id = selected_doc["material_id"]
    ehull_min = (
        float(selected_doc["energy_above_hull"])
        if selected_doc.get("energy_above_hull") is not None else float("inf")
    )
    retrieved_structure = _as_structure(selected_doc.get("structure"))

    # Step 4. Fetch initial-structure history only for the selected record.
    # use_document_model=False -> raw dicts, bypassing emmet-core's MPID
    # validation. The live MP API now returns alias ids like "mp-aaaaaaft"
    # that fail the strict MPID regex in every emmet-core version, raising
    # "Invalid MPID Format" and killing the whole query. Raw dicts avoid it.
    with MPRester(use_document_model=False) as mpr:
        mats_iter = mpr.materials.search(
            material_ids=[min_id],
            fields=["material_id", "initial_structures"],
        )
        mats = list(mats_iter)

    # Step 5. Merge results into final dict (attribute access).
    result = {
        "material_ids": [],
        "initial_structures": [],
        "relaxed_structures": [],
        "conventional_structure": [],
        "primitive_structure": [],
    }

    mdoc = next((item for item in mats if item.get("material_id") == min_id), None)
    available_initials = []
    if mdoc and mdoc.get("initial_structures"):
        available_initials = [_as_structure(item) for item in mdoc["initial_structures"]]
    if retrieved_structure is None:
        raise ValueError("Materials Project returned records but no usable periodic structure.")
    # 使用 SpacegroupAnalyzer 进行标准化处理
    sga = SpacegroupAnalyzer(retrieved_structure)

    # 1. 获取真正的原始胞 (LiNbO3 应该是 10 原子的那个)
    primitive = sga.get_primitive_standard_structure() 

    # 2. 获取常规胞 (LiNbO3 应该是 30 原子的那个)
    conventional = sga.get_conventional_standard_structure()

    # 存入结果
    result["primitive_structure"].append(primitive)
    result["conventional_structure"].append(conventional)

    result["material_ids"].append(min_id)
    # Some MP records do not expose an initial-structure history. The summary
    # endpoint structure is still a valid database starting geometry and must
    # keep the workflow usable rather than raising KeyError here.
    if available_initials:
        result["initial_structures"].append(available_initials[0].to(fmt="cif"))
    else:
        result["initial_structures"].append(retrieved_structure.to(fmt="cif"))
    result["relaxed_structures"].append(retrieved_structure)
    # result["conventional_structure"].append(conventional)
    # result["primitive_structure"].append(primitive)

    gt = {}
    try:
        s = relaxed_lookup.get(min_id)
        if s is not None:
            lat = s.lattice
            gt["a"] = lat.a
            gt["b"] = lat.b
            gt["c"] = lat.c
            gt["alpha"] = lat.alpha
            gt["beta"] = lat.beta
            gt["gamma"] = lat.gamma
            try:
                sga = SpacegroupAnalyzer(s, symprec=1e-3, angle_tolerance=5)
                gt["space_group"] = sga.get_space_group_symbol()
                gt["space_group_number"] = sga.get_space_group_number()
                gt["point_group"] = sga.get_point_group_symbol()
                gt["crystal_system"] = sga.get_crystal_system()
            except Exception:
                pass
    except Exception:
        pass
    result["ground_truth"] = gt

    # Compact, structured summary for the streamed log. The full structures used
    # to be printed via Structure.__repr__ — dozens of lines of site coordinates
    # that drowned out everything else. They're written to CIF files by the
    # caller instead, and surfaced behind a "details" toggle in the UI.
    summary = {"material_id": min_id}
    try:
        summary["formula"] = primitive.composition.reduced_formula
        summary["n_sites_primitive"] = len(primitive)
        summary["n_sites_conventional"] = len(conventional)
    except Exception:
        pass
    if ehull_min != float("inf"):
        summary["energy_above_hull"] = round(ehull_min, 4)
    for k in ("space_group", "space_group_number", "crystal_system", "point_group"):
        if k in gt:
            summary[k] = gt[k]
    for k in ("a", "b", "c", "alpha", "beta", "gamma"):
        if k in gt:
            summary[k] = round(gt[k], 4)
    result["summary"] = summary

    if verbose:
        sg = summary.get("space_group", "?")
        sgn = summary.get("space_group_number")
        sg_txt = f"{sg} (#{sgn})" if sgn else sg
        bits = [
            str(min_id),
            summary.get("formula", "?"),
            sg_txt,
            summary.get("crystal_system", "?"),
        ]
        print(f"[MP] {' · '.join(bits)}")
        if "a" in summary:
            print(f"[MP] a={summary['a']} b={summary['b']} c={summary['c']} Å · "
                  f"α={summary['alpha']}° β={summary['beta']}° γ={summary['gamma']}° · "
                  f"{summary.get('n_sites_primitive', '?')} sites (primitive)")

    # print("[MP result]", result)

    return result

# ---------- Example usage inside main() ----------

def main():
    snippet = 'mpr.materials.summary.search(formula="BaTiO3", spacegroup_symbol="P4mm")'
    out = fetch_material_info_from_api_snippet(snippet, limit=5)

    for mid, payload in out.items():
        print(f"\n================= {mid} =================")
        init_structs = payload["initial_structures"]

        if not init_structs:
            print("No initial structures found.")
        else:
            for i, s in enumerate(init_structs):
                print(f"\n# --- Initial structure {i} ---")
                print(s.to(fmt="cif"))  # print as CIF text directly

        if payload["relaxed_structure"]:
            print(f"\n# --- Relaxed structure ---")
            print(payload["relaxed_structure"].to(fmt="cif"))


if __name__ == "__main__":
    main()
