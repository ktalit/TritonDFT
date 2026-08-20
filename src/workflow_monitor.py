from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from results.electronic_reference import electronic_reference, electronic_references
from results.evidence_qa import evaluate_calculation, evidence_prompt, merge_evidence, parse_evidence_answer, parse_retrieval_plan, retrieval_plan_prompt, search_workflow_evidence, verify_evidence, workflow_file_manifest, workflow_inventory
from tool.structural_analysis import call_structural_analysis_tool, format_structural_tool_result


VESTA_DOWNLOAD_URL = "https://jp-minerals.org/vesta/en/download.html"


def _workflow_capabilities(run_dir: Path) -> set[str]:
    """Infer requested result panels from persisted workflow metadata.

    Metadata is authoritative for current workflows. Result-file discovery is
    used only for legacy directories that predate workflow plans/checkpoints.
    """
    steps: list[dict] = []
    state_path = run_dir / "workflow_state.json"
    plan_path = run_dir / "workflow_plan.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        steps = list(payload.get("plan") or payload.get("steps") or [])
    except (OSError, ValueError, TypeError):
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            steps = list(payload) if isinstance(payload, list) else []
        except (OSError, ValueError, TypeError):
            steps = []

    if steps:
        tools = {str(step.get("tool") or "").strip().lower() for step in steps}
        capabilities = set()
        if tools & {"pw_relax", "pw_vc_relax"}:
            capabilities.add("structure")
        if tools & {"pw_bands", "bands_post"}:
            capabilities.add("bands")
        if tools & {"dos_post"}:
            capabilities.add("dos")
        if tools & {"projwfc_post"}:
            capabilities.add("pdos")
        if "dynmat_post" in tools:
            capabilities.add("raman")
        if tools & {"q2r_post", "matdyn_post"}:
            capabilities.add("phonons")
        return capabilities

    # Legacy completed workflows may not have persisted a machine-readable plan.
    capabilities = set()
    if _relaxed_structure_path(run_dir) is not None:
        capabilities.add("structure")
    if _bands_data(run_dir) is not None:
        capabilities.add("bands")
    if _dos_data(run_dir) is not None:
        capabilities.add("dos")
    return capabilities


def _calculation_input_files(run_dir: Path) -> list[Path]:
    """Inputs actually materialized for execution, with sensible fallbacks."""
    all_inputs = [path for path in sorted(run_dir.rglob("*.in"))
                  if path.is_file() and not any(part.endswith(".save") for part in path.parts)]
    attempted = [path for path in all_inputs if "attempts" in {part.lower() for part in path.parts}]
    if attempted:
        return attempted
    branched = [path for path in all_inputs if "branches" in {part.lower() for part in path.parts}]
    if branched:
        return branched
    approved = [path for path in all_inputs if "approved_inputs" in {part.lower() for part in path.parts}]
    return approved or all_inputs


def _input_tab_label(run_dir: Path, path: Path) -> str:
    relative = path.relative_to(run_dir)
    parts = relative.parts
    if "attempts" in parts:
        index = parts.index("attempts")
        tail = parts[index + 1:]
        if len(tail) >= 3:
            return f"{tail[0]}/{tail[1]}/{tail[-1]}"
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return path.name


def _bands_data(run_dir: Path):
    paths = sorted(run_dir.rglob("*.band.gnu")) + sorted(run_dir.rglob("*.bands.dat.gnu"))
    if not paths:
        return None
    bands, current = [], []
    for raw in paths[0].read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            if current:
                bands.append(current)
                current = []
            continue
        try:
            x, energy = map(float, raw.split()[:2])
        except (ValueError, IndexError):
            continue
        current.append((x, energy))
    if current:
        bands.append(current)
    return (paths[0], bands) if bands else None


def _band_symmetry_ticks(run_dir: Path) -> list[tuple[float, str]]:
    """Return verified bands.x path coordinates paired with saved labels."""
    labels_path = run_dir / "band_path_labels.json"
    try:
        payload = json.loads(labels_path.read_text(encoding="utf-8"))
        labels = [str(label).strip() for label in payload]
    except (OSError, ValueError, TypeError):
        return []
    if not labels:
        return []

    # bands.x reports the exact abscissa used by the .gnu file. Prefer the
    # purpose-named output, while retaining qe.out as a legacy fallback.
    outputs = sorted(
        run_dir.rglob("*.out"),
        key=lambda path: (
            "bands-post" not in path.name.lower(),
            "attempts" not in {part.lower() for part in path.parts},
            str(path),
        ),
    )
    coordinate_re = re.compile(
        r"high-symmetry\s+point:.*?x\s+coordinate\s+([-+0-9.Ee]+)", re.I
    )
    for output in outputs:
        text = output.read_text(encoding="utf-8", errors="replace")
        coordinates = [float(value) for value in coordinate_re.findall(text)]
        if len(coordinates) == len(labels):
            ticks = []
            for coordinate, label in zip(coordinates, labels):
                display = r"$\Gamma$" if label.replace("\\", "").lower() == "gamma" else label
                ticks.append((coordinate, display))
            return ticks
    return []


def _dos_data(run_dir: Path):
    paths = [path for path in sorted(run_dir.rglob("*.dos")) if "pdos_" not in path.name.lower()]
    if not paths:
        return None
    rows = []
    for raw in paths[0].read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            values = [float(value) for value in raw.split()]
        except ValueError:
            continue
        if len(values) >= 2:
            rows.append(values)
    return (paths[0], rows) if rows else None


def _pdos_data(run_dir: Path):
    """Aggregate QE projwfc.x files into species/orbital plot series."""
    grouped = defaultdict(list)
    paths = []
    pattern = re.compile(r"atm#\d+\(([^)]+)\).*wfc#\d+\(([spdf])", re.I)
    for path in sorted(run_dir.rglob("*pdos_atm*")):
        if not path.is_file():
            continue
        match = pattern.search(path.name)
        if not match:
            continue
        rows = []
        header = ""
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.lstrip().startswith("#"):
                header += " " + raw.lower()
                continue
            try:
                values = [float(value) for value in raw.split()]
            except ValueError:
                continue
            if len(values) >= 2:
                rows.append(values)
        if rows:
            species = re.sub(r"_(?:up|down)$", "", match.group(1), flags=re.I)
            grouped[(species, match.group(2).lower())].append(
                (rows, "ldosup" in header or "pdosup" in header)
            )
            paths.append(path)
    series = []
    for (species, orbital), datasets in sorted(grouped.items()):
        length = min(len(rows) for rows, _spin in datasets)
        if not length:
            continue
        energy = [datasets[0][0][index][0] for index in range(length)]
        spin_polarized = any(spin for _rows, spin in datasets)
        up = [sum(rows[index][1] for rows, _spin in datasets) for index in range(length)]
        down = None
        if spin_polarized and all(min(len(row) for row in rows[:length]) >= 3 for rows, _spin in datasets):
            down = [sum(rows[index][2] for rows, _spin in datasets) for index in range(length)]
        series.append((f"{species}-{orbital}", energy, up, down))
    return (paths, series) if series else None


def _float_or_none(value: str):
    value = value.strip()
    return None if not value else float(value)


def _write_plot_settings(run_dir: Path, kind: str, settings: dict) -> None:
    path = run_dir / "plot_settings.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        payload = {}
    payload[kind] = settings
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class PlotPanel(ttk.Frame):
    def __init__(self, parent, run_dir: Path, kind: str):
        super().__init__(parent)
        self.run_dir = run_dir
        self.kind = kind
        self.loaded_path = ""
        self.figure = self.axes = self.canvas = None
        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=6, pady=6)
        self.entries = {}
        defaults = (
            {"xmin": "", "xmax": "", "ymin": "-10", "ymax": "10"}
            if kind == "bands" else
            {"xmin": "-10", "xmax": "10", "ymin": "", "ymax": ""}
        )
        for label, key, default in (
            ("X min", "xmin", defaults["xmin"]), ("X max", "xmax", defaults["xmax"]),
            ("Y min", "ymin", defaults["ymin"]), ("Y max", "ymax", defaults["ymax"]),
        ):
            ttk.Label(controls, text=label).pack(side="left", padx=(5, 2))
            entry = ttk.Entry(controls, width=8)
            entry.insert(0, default)
            entry.pack(side="left")
            self.entries[key] = entry
        ttk.Label(controls, text="Reference").pack(side="left", padx=(10, 2))
        self.reference = ttk.Combobox(
            controls, values=("VBM", "Fermi", "Midgap", "Absolute"), width=9, state="readonly"
        )
        self.reference.set("VBM")
        self.reference.pack(side="left")
        ttk.Button(controls, text="Apply", command=self.draw).pack(side="left", padx=(10, 2))
        ttk.Button(controls, text="Reset", command=self.reset).pack(side="left", padx=2)
        ttk.Button(controls, text="Save PNG", command=lambda: self.save("png")).pack(side="left", padx=2)
        ttk.Button(controls, text="Save PDF", command=lambda: self.save("pdf")).pack(side="left", padx=2)
        self.note = ttk.Label(self, text="Waiting for downloaded numerical data…", wraplength=940)
        self.note.pack(fill="x", padx=8, pady=(0, 5))
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            self.figure = Figure(figsize=(7.4, 5.2), dpi=100)
            self.axes = self.figure.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.figure, master=self)
            self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)
        except Exception as exc:
            self.note.configure(text=f"Matplotlib preview unavailable: {exc}")

    def reset(self):
        defaults = (
            {"xmin": "", "xmax": "", "ymin": "-10", "ymax": "10"}
            if self.kind == "bands" else
            {"xmin": "-10", "xmax": "10", "ymin": "", "ymax": ""}
        )
        for key, entry in self.entries.items():
            entry.delete(0, "end")
            entry.insert(0, defaults[key])
        self.reference.set("VBM")
        self.draw()

    def _limits(self):
        return {key: _float_or_none(entry.get()) for key, entry in self.entries.items()}

    def draw(self):
        if self.axes is None:
            return
        try:
            limits = self._limits()
        except ValueError:
            messagebox.showerror("Invalid plot limit", "Plot limits must be numbers or blank for automatic scaling.")
            return
        source = (
            _bands_data(self.run_dir) if self.kind == "bands" else
            _pdos_data(self.run_dir) if self.kind == "pdos" else
            _dos_data(self.run_dir)
        )
        if not source:
            missing = {
                "bands": "No downloaded band data found.",
                "pdos": "No downloaded projected-DOS data found.",
                "dos": "No downloaded DOS data found.",
            }
            self.note.configure(text=missing[self.kind])
            return
        path, data = source
        self.loaded_path = "|".join(str(item) for item in path) if self.kind == "pdos" else str(path)
        mode = self.reference.get().lower()
        refs = electronic_references(self.run_dir)
        reference = electronic_reference(self.run_dir, mode)
        self.axes.clear()
        if self.kind == "bands":
            for band in data:
                self.axes.plot([point[0] for point in band], [point[1] - reference for point in band], color="black", lw=0.9)
            self.axes.axhline(0, color="tab:red", ls="--", lw=0.8)
            symmetry_ticks = _band_symmetry_ticks(self.run_dir)
            if symmetry_ticks:
                positions, labels = zip(*symmetry_ticks)
                self.axes.set_xticks(positions)
                self.axes.set_xticklabels(labels)
                for position in positions:
                    self.axes.axvline(position, color="0.75", lw=0.6, zorder=0)
                self.axes.set_xlim(positions[0], positions[-1])
                self.axes.set_xlabel("High-symmetry k-path")
            else:
                self.axes.set_xlabel("k-path distance")
            self.axes.set_ylabel(f"Energy - {self.reference.get()} (eV)" if mode != "absolute" else "Absolute energy (eV)")
            self.axes.set_title("Electronic band structure")
        elif self.kind == "dos":
            energies = [row[0] - reference for row in data]
            ncol = min(len(row) for row in data)
            if ncol >= 4:
                self.axes.plot(energies, [row[1] for row in data], label="spin up")
                self.axes.plot(energies, [-row[2] for row in data], label="spin down")
                self.axes.legend()
            else:
                self.axes.plot(energies, [row[1] for row in data], color="black")
            self.axes.axvline(0, color="tab:red", ls="--", lw=0.8)
            self.axes.set_xlabel(f"Energy - {self.reference.get()} (eV)" if mode != "absolute" else "Absolute energy (eV)")
            self.axes.set_ylabel("DOS (states/eV)")
            self.axes.set_title("Total density of states")
        else:
            for label, raw_energy, up, down in data:
                energies = [energy - reference for energy in raw_energy]
                self.axes.plot(energies, up, label=label)
                if down is not None:
                    self.axes.plot(energies, [-value for value in down], ls="--", label=f"{label} down")
            self.axes.axvline(0, color="tab:red", ls="--", lw=0.8)
            self.axes.set_xlabel(f"Energy - {self.reference.get()} (eV)" if mode != "absolute" else "Absolute energy (eV)")
            self.axes.set_ylabel("Projected DOS (states/eV)")
            self.axes.set_title("Orbital-projected density of states")
            self.axes.legend(fontsize=8, ncol=2)
        if limits["xmin"] is not None or limits["xmax"] is not None:
            self.axes.set_xlim(left=limits["xmin"], right=limits["xmax"])
        if limits["ymin"] is not None or limits["ymax"] is not None:
            self.axes.set_ylim(bottom=limits["ymin"], top=limits["ymax"])
        self.figure.tight_layout()
        self.canvas.draw_idle()
        available = "available references: " + ", ".join(f"{key}={value:.6g} eV" for key, value in sorted(refs.items()))
        source_text = f"{len(path)} projwfc files" if self.kind == "pdos" else path.name
        self.note.configure(text=f"Source: {source_text}; {available}")
        _write_plot_settings(self.run_dir, self.kind, {
            **limits, "reference": self.reference.get(), "reference_energy_ev": reference,
            "source": [str(item) for item in path] if self.kind == "pdos" else str(path),
        })

    def refresh_if_available(self):
        source = (
            _bands_data(self.run_dir) if self.kind == "bands" else
            _pdos_data(self.run_dir) if self.kind == "pdos" else
            _dos_data(self.run_dir)
        )
        signature = "|".join(str(item) for item in source[0]) if source and self.kind == "pdos" else str(source[0]) if source else ""
        if source and signature != self.loaded_path:
            self.draw()

    def save(self, extension: str):
        if self.figure is None or not self.loaded_path:
            messagebox.showinfo("No plot", "Download the numerical results and generate the plot first.")
            return
        default = {
            "bands": "band_structure_custom",
            "dos": "total_dos_custom",
            "pdos": "projected_dos_custom",
        }[self.kind]
        path = filedialog.asksaveasfilename(
            initialdir=str(self.run_dir), initialfile=f"{default}.{extension}",
            defaultextension=f".{extension}", filetypes=[(extension.upper(), f"*.{extension}")],
        )
        if path:
            self.figure.savefig(path, dpi=300, bbox_inches="tight")


def _structure_text(run_dir: Path) -> str:
    candidates = sorted(run_dir.rglob("relaxed_structure.in"))
    if not candidates:
        return "Relaxed structure has not been downloaded yet.\n"
    text = candidates[-1].read_text(encoding="utf-8", errors="replace")
    rows = re.search(r"(?mis)CELL_PARAMETERS\s*\([^)]*\)\s*\n([^\n]+)\n([^\n]+)\n([^\n]+)", text)
    summary = []
    if rows:
        try:
            vectors = [[float(value) for value in row.split()[:3]] for row in rows.groups()]
            lengths = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
            volume = abs(
                vectors[0][0] * (vectors[1][1] * vectors[2][2] - vectors[1][2] * vectors[2][1])
                - vectors[0][1] * (vectors[1][0] * vectors[2][2] - vectors[1][2] * vectors[2][0])
                + vectors[0][2] * (vectors[1][0] * vectors[2][1] - vectors[1][1] * vectors[2][0])
            )
            summary.append(f"Lattice lengths: a={lengths[0]:.6f} Å, b={lengths[1]:.6f} Å, c={lengths[2]:.6f} Å")
            summary.append(f"Cell volume: {volume:.6f} Å³")
        except (ValueError, IndexError):
            pass
    return ("\n".join(summary) + "\n\n" if summary else "") + text


def _relaxed_structure_path(run_dir: Path) -> Path | None:
    candidates = sorted(run_dir.rglob("relaxed_structure.in"))
    return candidates[-1] if candidates else None


def _relaxed_structure_to_cif(run_dir: Path, destination: Path | None = None) -> Path:
    source = _relaxed_structure_path(run_dir)
    if source is None:
        raise FileNotFoundError("The relaxed structure has not been downloaded.")
    from structure_paths import _parse_relaxed_structure
    from pymatgen.io.cif import CifWriter
    structure = _parse_relaxed_structure(source.read_text(encoding="utf-8", errors="replace"))
    target = destination or (run_dir / "relaxed_structure.cif")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write explicitly in text mode; recent monty versions reject pymatgen's
    # legacy implicit mode in Structure.to(filename=...).
    target.write_text(str(CifWriter(structure)), encoding="utf-8")
    return target


def _saved_vesta_path(run_dir: Path) -> str:
    path = run_dir / "viewer_settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("vesta_path", "")
    except (OSError, ValueError):
        return ""
    return _resolve_vesta_location(Path(value)) if value else ""


def _resolve_vesta_location(selected: Path) -> str:
    """Resolve an app/executable or a directory containing VESTA."""
    selected = selected.expanduser()
    if not selected.exists():
        return ""
    if sys.platform == "darwin":
        if selected.is_dir() and selected.suffix.lower() == ".app":
            return str(selected.resolve())
        if selected.is_dir():
            direct = selected / "VESTA.app"
            if direct.is_dir():
                return str(direct.resolve())
            # Distribution folders occasionally add one enclosing version
            # directory. Keep this bounded rather than scanning the home tree.
            for child in selected.iterdir():
                if child.is_dir() and child.name.lower() == "vesta.app":
                    return str(child.resolve())
                if child.is_dir() and child.suffix.lower() != ".app":
                    nested = child / "VESTA.app"
                    if nested.is_dir():
                        return str(nested.resolve())
        return ""
    if selected.is_file():
        return str(selected.resolve())
    executable_names = ("VESTA.exe", "vesta.exe") if os.name == "nt" else ("VESTA", "vesta")
    for name in executable_names:
        candidate = selected / name
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


def _find_vesta(run_dir: Path) -> str:
    saved = _saved_vesta_path(run_dir)
    if saved:
        return saved
    for command in ("VESTA", "vesta"):
        resolved = shutil.which(command)
        if resolved:
            return resolved
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/VESTA.app"),
            Path.home() / "Applications/VESTA.app",
            Path.home() / "VESTA",
            Path.home() / "vesta",
        ]
    elif os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "VESTA" / "VESTA.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "VESTA" / "VESTA.exe",
        ]
    else:
        repository_root = Path(__file__).resolve().parents[1]
        candidates = [
            repository_root / "local" / "VESTA",
            Path("/usr/bin/VESTA"),
            Path("/usr/local/bin/VESTA"),
        ]
    for path in candidates:
        resolved = _resolve_vesta_location(path)
        if resolved:
            return resolved
    return ""


def _launch_vesta(application: str, cif_path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", application, str(cif_path)])
    elif os.name == "nt":
        subprocess.Popen([application, str(cif_path)])
    else:
        subprocess.Popen([application, str(cif_path)])


def _save_vesta_path(run_dir: Path, application: str) -> None:
    (run_dir / "viewer_settings.json").write_text(
        json.dumps({"vesta_path": application}, indent=2) + "\n", encoding="utf-8"
    )


def run_monitor(run_dir: Path) -> None:
    capabilities = _workflow_capabilities(run_dir)
    root = tk.Tk()
    root.title(f"TritonDFT workflow — {run_dir.name}")
    root.geometry("1100x720")
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    status_frame = ttk.Frame(notebook)
    tree = ttk.Treeview(status_frame, columns=("id", "task", "branch", "state", "attempt", "jobs"), show="headings")
    for column, width in (("id", 45), ("task", 330), ("branch", 150), ("state", 110), ("attempt", 80), ("jobs", 180)):
        tree.heading(column, text=column.title())
        tree.column(column, width=width, anchor="w")
    tree.pack(fill="both", expand=True)
    status_label = ttk.Label(status_frame, text="Waiting for workflow state…")
    status_label.pack(fill="x", pady=6)
    error_label = ttk.Label(status_frame, text="", wraplength=1040, justify="left")
    error_label.pack(fill="x", pady=(0, 6))
    notebook.add(status_frame, text="Execution status")

    validation = tk.Text(notebook, wrap="word", font=("Menlo", 11))
    validation.configure(state="disabled")
    notebook.add(validation, text="Validation")
    structure_frame = ttk.Frame(notebook)
    structure_controls = ttk.Frame(structure_frame)
    structure_controls.pack(fill="x", padx=6, pady=6)
    structure = tk.Text(structure_frame, wrap="none", font=("Menlo", 11))
    structure.pack(fill="both", expand=True, padx=6, pady=(0, 6))
    structure.configure(state="disabled")
    if "structure" in capabilities:
        notebook.add(structure_frame, text="Structure")

    def save_cif_as() -> None:
        source = _relaxed_structure_path(run_dir)
        if source is None:
            messagebox.showinfo("Structure unavailable", "Fetch the relaxed structure before exporting it.")
            return
        destination = filedialog.asksaveasfilename(
            initialdir=str(run_dir), initialfile="relaxed_structure.cif",
            defaultextension=".cif", filetypes=[("Crystallographic Information File", "*.cif")],
        )
        if destination:
            try:
                _relaxed_structure_to_cif(run_dir, Path(destination))
            except Exception as exc:
                messagebox.showerror("CIF export failed", str(exc))

    def locate_vesta() -> None:
        if sys.platform == "darwin":
            selected = filedialog.askdirectory(title="Select VESTA.app")
        else:
            selected = filedialog.askopenfilename(title="Select the VESTA executable")
        if not selected:
            return
        resolved = _resolve_vesta_location(Path(selected))
        if not resolved:
            messagebox.showerror(
                "VESTA not found",
                "The selected location does not contain VESTA.app or a VESTA executable. "
                "Select either VESTA.app itself or the folder that contains it.",
            )
            return
        _save_vesta_path(run_dir, resolved)
        messagebox.showinfo("VESTA located", f"Saved VESTA location:\n{resolved}")

    def open_vesta() -> None:
        try:
            cif = _relaxed_structure_to_cif(run_dir)
        except Exception as exc:
            messagebox.showinfo("Structure unavailable", str(exc))
            return
        application = _find_vesta(run_dir)
        if not application:
            if messagebox.askyesno(
                "VESTA not found",
                "VESTA is not installed or could not be located. Open the official download page?",
            ):
                webbrowser.open(VESTA_DOWNLOAD_URL)
            return
        try:
            _launch_vesta(application, cif)
        except OSError as exc:
            messagebox.showerror("Could not open VESTA", f"{exc}\n\nUse 'Locate VESTA…' to select it manually.")

    def open_structure_folder() -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(run_dir)])
            elif os.name == "nt":
                subprocess.Popen(["explorer", str(run_dir)])
            else:
                subprocess.Popen(["xdg-open", str(run_dir)])
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc))

    ttk.Button(structure_controls, text="Open in VESTA", command=open_vesta).pack(side="left", padx=2)
    ttk.Button(structure_controls, text="Locate VESTA…", command=locate_vesta).pack(side="left", padx=2)
    ttk.Button(
        structure_controls, text="Download VESTA", command=lambda: webbrowser.open(VESTA_DOWNLOAD_URL)
    ).pack(side="left", padx=2)
    ttk.Button(structure_controls, text="Save CIF As…", command=save_cif_as).pack(side="left", padx=2)
    ttk.Button(structure_controls, text="Open folder", command=open_structure_folder).pack(side="left", padx=2)
    bands_panel = None
    if "bands" in capabilities:
        bands_panel = PlotPanel(notebook, run_dir, "bands")
        notebook.add(bands_panel, text="Band structure")
    dos_panel = None
    if "dos" in capabilities:
        dos_panel = PlotPanel(notebook, run_dir, "dos")
        notebook.add(dos_panel, text="DOS")
    pdos_panel = None
    if "pdos" in capabilities:
        pdos_panel = PlotPanel(notebook, run_dir, "pdos")
        notebook.add(pdos_panel, text="PDOS")

    inputs_frame = ttk.Frame(notebook)
    inputs_note = ttk.Label(inputs_frame, text="Concrete input files used by workflow execution attempts.", wraplength=1040)
    inputs_note.pack(fill="x", padx=8, pady=(8, 4))
    inputs_notebook = ttk.Notebook(inputs_frame)
    inputs_notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    notebook.add(inputs_frame, text="Input files")
    input_signature = {"value": None}

    def refresh_input_files() -> None:
        paths = _calculation_input_files(run_dir)
        signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
        if signature == input_signature["value"]:
            return
        input_signature["value"] = signature
        for tab_id in inputs_notebook.tabs():
            inputs_notebook.nametowidget(tab_id).destroy()
        if not paths:
            empty = ttk.Frame(inputs_notebook)
            ttk.Label(empty, text="No calculation input files have been materialized yet.").pack(padx=12, pady=12)
            inputs_notebook.add(empty, text="No inputs")
            inputs_note.configure(text="No concrete calculation inputs found yet.")
            return
        for path in paths:
            frame = ttk.Frame(inputs_notebook)
            header = ttk.Frame(frame)
            header.pack(fill="x", padx=6, pady=6)
            ttk.Label(header, text=str(path), font=("Menlo", 10)).pack(side="left", fill="x", expand=True)
            text_widget = tk.Text(frame, wrap="none", font=("Menlo", 11), undo=False)
            y_scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
            x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=text_widget.xview)
            text_widget.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
            y_scroll.pack(side="right", fill="y")
            x_scroll.pack(side="bottom", fill="x")
            text_widget.pack(fill="both", expand=True, padx=(6, 0), pady=(0, 6))
            text_widget.insert("1.0", path.read_text(encoding="utf-8", errors="replace"))
            text_widget.configure(state="disabled")
            inputs_notebook.add(frame, text=_input_tab_label(run_dir, path))
        inputs_note.configure(text=f"Showing {len(paths)} concrete input file(s). Tabs include step and attempt so repaired inputs remain distinguishable.")

    ask_frame = ttk.Frame(notebook)
    ask_top = ttk.Frame(ask_frame)
    ask_top.pack(fill="x", padx=8, pady=8)
    ttk.Label(ask_top, text="Question about these calculated files:").pack(side="left")
    ask_question = ttk.Entry(ask_top)
    ask_question.pack(side="left", fill="x", expand=True, padx=8)
    ask_button = ttk.Button(ask_top, text="Ask")
    ask_button.pack(side="right")
    ttk.Label(ask_top, text="Sources").pack(side="left", padx=(8, 2))
    ask_scope = ttk.Combobox(
        ask_top, values=("Completed results", "Include failed attempts"), width=20, state="readonly"
    )
    ask_scope.set("Completed results")
    ask_scope.pack(side="right", padx=(2, 8))
    ask_status = ttk.Label(ask_frame, text="Search scope: text results inside this workflow only. Relevant excerpts may be sent to the configured LLM.", wraplength=1040, justify="left")
    ask_status.pack(fill="x", padx=8, pady=(0, 6))
    ask_answer = tk.Text(ask_frame, wrap="word", font=("Menlo", 11))
    ask_answer.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    ask_answer.configure(state="disabled")
    notebook.add(ask_frame, text="Ask Results")

    evidence_consent = {"given": False}

    def set_ask_text(text: str) -> None:
        ask_answer.configure(state="normal")
        ask_answer.delete("1.0", "end")
        ask_answer.insert("1.0", text)
        ask_answer.configure(state="disabled")

    def finish_ask(text: str, status: str) -> None:
        set_ask_text(text)
        ask_status.configure(text=status)
        ask_button.configure(state="normal")

    def ask_worker(question: str, include_failed: bool) -> None:
        try:
            structural = call_structural_analysis_tool(run_dir, question)
            if structural is not None:
                output = format_structural_tool_result(structural)
                root.after(0, lambda value=output: finish_ask(value, "Answered by the deterministic pymatgen structural-analysis tool."))
                return
            from generator import UnifiedGenerator
            generator = UnifiedGenerator(model=os.environ.get("CLUSTER_AGENT_MODEL", "gpt-4o"), backend=os.environ.get("CLUSTER_AGENT_BACKEND", "auto"), temperature=0.0)
            inventory = workflow_inventory(run_dir)
            manifest = workflow_file_manifest(run_dir, include_failed=include_failed)
            plan_response = generator(
                retrieval_plan_prompt(question, inventory, manifest), max_new_tokens=450
            )
            plan_raw = plan_response[0].get("generated_text", "") if plan_response else ""
            retrieval_queries = parse_retrieval_plan(plan_raw)
            groups = [search_workflow_evidence(
                run_dir, question, include_failed=include_failed
            )]
            for retrieval_query in retrieval_queries:
                groups.append(search_workflow_evidence(
                    run_dir, retrieval_query, limit=12, include_failed=include_failed
                ))
            evidence = merge_evidence(groups)
            if not evidence:
                root.after(0, lambda: finish_ask("I could not find relevant evidence in the downloaded workflow files. This question cannot be answered from the available calculation record.", "No supporting lines found."))
                return
            response = generator(
                evidence_prompt(question, evidence, inventory), max_new_tokens=1100
            )
            raw = response[0].get("generated_text", "") if response else ""
            parsed = parse_evidence_answer(raw, {item.evidence_id for item in evidence})
            by_id = {item.evidence_id: item for item in evidence}
            cited = [by_id[item_id] for item_id in parsed["evidence_ids"] if verify_evidence(by_id[item_id])]
            if cited:
                output = (
                    f"Answer ({parsed['answer_type'].capitalize()})\n{parsed['answer']}\n\n"
                    f"Confidence: {parsed['confidence']}\n"
                )
                if parsed["derivation"]:
                    output += f"\nDerivation / interpretation\n{parsed['derivation']}\n"
                expression = parsed["calculation"]["expression"]
                if expression:
                    try:
                        calculated = evaluate_calculation(expression)
                        description = parsed["calculation"]["description"] or "calculated value"
                        unit = parsed["calculation"]["unit"]
                        output += f"\nTritonDFT calculated result\n{description}: {calculated:.10g}{(' ' + unit) if unit else ''}\nExpression: {expression}\n"
                    except (ValueError, ArithmeticError) as exc:
                        output += f"\nCalculation was not accepted: {exc}\n"
                output += "\nVerified supporting evidence\n"
            else:
                output = "The model did not provide a verifiable citation, so its proposed answer was not accepted.\n\nMost relevant verified source lines:\n"
                cited = [item for item in evidence[:5] if verify_evidence(item)]
            for item in cited:
                output += f"\n[{item.evidence_id}] {item.path}:{item.line_number}\n{item.line}\n"
            root.after(0, lambda value=output: finish_ask(value, "Answer completed from workflow-local evidence."))
        except Exception as exc:
            root.after(0, lambda value=str(exc): finish_ask(f"The evidence search completed, but the LLM answer could not be generated.\n\n{value}", "Question failed; no unsupported answer was shown."))

    def ask_results(_event=None) -> None:
        question = ask_question.get().strip()
        if not question:
            messagebox.showinfo("Question required", "Enter a question about this workflow's results.")
            return
        if not evidence_consent["given"]:
            if not messagebox.askyesno("Send calculation excerpts?", "TritonDFT will search only this workflow directory. Relevant text excerpts, which may contain cluster paths or job metadata, will be sent to your configured LLM provider. Continue?"):
                return
            evidence_consent["given"] = True
        ask_button.configure(state="disabled")
        include_failed = ask_scope.get() == "Include failed attempts"
        scope_text = "including failed attempts" if include_failed else "completed attempts only"
        ask_status.configure(text=f"Searching workflow files and checking evidence ({scope_text})…")
        set_ask_text("Working…")
        threading.Thread(target=ask_worker, args=(question, include_failed), daemon=True).start()

    ask_button.configure(command=ask_results)
    ask_question.bind("<Return>", ask_results)

    controls = ttk.Frame(root)
    controls.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Label(controls, text="Closing this monitor does not cancel cluster jobs.").pack(side="left")
    ttk.Button(controls, text="Close", command=root.destroy).pack(side="right")

    last_structure = ""
    def refresh() -> None:
        nonlocal last_structure
        try:
            state = json.loads((run_dir / "workflow_state.json").read_text(encoding="utf-8"))
            for item in tree.get_children():
                tree.delete(item)
            for step in state.get("steps", []):
                tree.insert("", "end", values=(step.get("id"), step.get("problem"), step.get("branch"), step.get("status"), step.get("attempts", 0), ", ".join(step.get("job_ids", []))))
            status_label.configure(text=f"Workflow: {state.get('status', 'unknown')}   Updated: {state.get('updated_at', '')}")
            waiting = next((step for step in state.get("steps", []) if step.get("status") == "awaiting_user"), None)
            error_label.configure(text=(f"Terminal awaiting recovery instruction for step {waiting.get('id')}.\nLatest error: {waiting.get('last_error', '')}" if waiting else ""))
        except (OSError, ValueError):
            pass
        report_path = run_dir / "validation_report.txt"
        report = report_path.read_text(encoding="utf-8", errors="replace") if report_path.is_file() else "No validation report yet."
        validation.configure(state="normal"); validation.delete("1.0", "end"); validation.insert("1.0", report); validation.configure(state="disabled")
        current_structure = _structure_text(run_dir)
        if current_structure != last_structure:
            last_structure = current_structure
            structure.configure(state="normal"); structure.delete("1.0", "end"); structure.insert("1.0", current_structure); structure.configure(state="disabled")
        if bands_panel is not None:
            bands_panel.refresh_if_available()
        if dos_panel is not None:
            dos_panel.refresh_if_available()
        if pdos_panel is not None:
            pdos_panel.refresh_if_available()
        refresh_input_files()
        root.after(2000, refresh)

    refresh()
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep a live TritonDFT workflow and results window open.")
    parser.add_argument("run_dir")
    args = parser.parse_args()
    run_monitor(Path(args.run_dir).expanduser().resolve())


if __name__ == "__main__":
    main()
