"""Standalone plotting agent for common VASP electronic-structure outputs.

The plotter is deliberately independent from workflow execution. Point it at a
completed directory containing ``vasprun.xml`` and it writes publication-ready
PNG plots without submitting jobs or calling an LLM.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


def _load_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _load_vasprun(path: Path, *, projected: bool = False):
    from pymatgen.io.vasp.outputs import Vasprun
    return Vasprun(str(path), parse_projected_eigen=projected)


def _first_file(root: Path, name: str) -> Path:
    direct = root / name
    if direct.is_file():
        return direct
    matches = sorted(root.rglob(name))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Could not find {name} under {root}")


def _save(fig, output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight")
    fig.clf()
    return str(output)


def _label_ticks(band_structure: Any) -> tuple[list[float], list[str]]:
    def clean_label(value: Any) -> str:
        # Tolerate legacy/generated KPOINTS lines containing an extra point
        # count before the comment marker, e.g. ``... 10 ! Gamma``.
        return re.sub(r"^\s*\d+\s*!\s*", "", str(value)).strip()

    distances = getattr(band_structure, "distance", [])
    kpoints = getattr(band_structure, "kpoints", []) or []
    if kpoints and len(kpoints) == len(distances):
        ticks: list[float] = []
        labels: list[str] = []
        for index, kpoint in enumerate(kpoints):
            raw_label = getattr(kpoint, "label", None)
            if not raw_label:
                continue
            tick = float(distances[index])
            label = clean_label(raw_label)
            if ticks and tick == ticks[-1]:
                if label != labels[-1]:
                    labels[-1] = f"{labels[-1]}|{label}"
            else:
                ticks.append(tick)
                labels.append(label)
        if ticks:
            return ticks, labels

    # ``BandStructureSymmLine.labels_dict`` maps labels to Kpoint objects, not
    # distances.  Branch indices are the stable public data needed to place
    # labels on the one-dimensional plotting path.
    branches = getattr(band_structure, "branches", []) or []
    if branches and len(distances):
        ticks: list[float] = []
        labels: list[str] = []
        for branch in branches:
            start = int(branch["start_index"])
            end = int(branch["end_index"])
            names = [clean_label(name) for name in str(branch["name"]).split("-", 1)]
            if len(names) != 2 or names[0] == names[1]:
                continue
            branch_ticks = [float(distances[start]), float(distances[end])]
            for tick, label in zip(branch_ticks, names):
                if ticks and tick == ticks[-1]:
                    if label != labels[-1]:
                        labels[-1] = f"{labels[-1]}|{label}"
                else:
                    ticks.append(tick)
                    labels.append(label)
        return ticks, labels

    # Retain support for simple mocked/custom band objects whose label values
    # are already numeric distances.
    label_map = getattr(band_structure, "labels_dict", {}) or {}
    numeric_pairs = []
    for label, distance in label_map.items():
        try:
            numeric_pairs.append((float(distance), str(label)))
        except (TypeError, ValueError):
            continue
    if numeric_pairs:
        numeric_pairs.sort()
        return [item[0] for item in numeric_pairs], [item[1] for item in numeric_pairs]
    return [], []


class VASPPlotter:
    """Create common VASP electronic-structure plots from ``vasprun.xml``."""

    def __init__(self, run_dir: str | Path, output_dir: str | Path | None = None):
        self.run_dir = Path(run_dir).expanduser().resolve()
        if not self.run_dir.is_dir():
            raise NotADirectoryError(f"VASP run directory does not exist: {self.run_dir}")
        self.output_dir = Path(output_dir or self.run_dir / "plots").expanduser().resolve()
        self._vasprun = None

    @property
    def vasprun(self):
        if self._vasprun is None:
            self._vasprun = _load_vasprun(_first_file(self.run_dir, "vasprun.xml"))
        return self._vasprun

    def _band_structure(self, *, projected: bool = False):
        vasprun = self.vasprun if not projected else _load_vasprun(
            _first_file(self.run_dir, "vasprun.xml"), projected=True
        )
        try:
            return vasprun.get_band_structure(line_mode=True)
        except Exception as exc:
            raise RuntimeError(
                "VASP band plotting requires a line-mode vasprun.xml with band data."
            ) from exc

    def plot_band_structure(self, *, reference: str = "fermi") -> str:
        plt = _load_matplotlib()
        bands = self._band_structure()
        reference_value = float(getattr(bands, "efermi", 0.0)) if reference.lower() == "fermi" else 0.0
        fig, ax = plt.subplots(figsize=(7.2, 5.2))
        for energies in bands.bands.values():
            for band in energies:
                ax.plot(
                    bands.distance,
                    [float(value) - reference_value for value in band],
                    color="black",
                    linewidth=0.8,
                )
        ticks, labels = _label_ticks(bands)
        if ticks:
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels)
            for tick in ticks:
                ax.axvline(tick, color="0.75", linewidth=0.6)
        ax.axhline(0.0, color="tab:red", linestyle="--", linewidth=0.8)
        ax.set_xlabel("High-symmetry k path")
        ax.set_ylabel(f"Energy - {reference.title()} (eV)")
        ax.set_title("VASP band structure")
        return _save(fig, self.output_dir / "vasp_band_structure.png")

    def plot_projected_band_structure(
        self,
        *,
        element: str | None = None,
        orbital: str | None = None,
        reference: str = "fermi",
    ) -> str:
        plt = _load_matplotlib()
        bands = self._band_structure(projected=True)
        species = sorted({str(site.specie) for site in bands.structure})
        selected_elements = [element] if element else species
        selected_elements = [name for name in selected_elements if name in species]
        if not selected_elements:
            raise ValueError("No matching element projections were found in the VASP band data.")
        selected_orbitals = [orbital.lower()] if orbital else ["s", "p", "d", "f"]
        projection_spec = {name: selected_orbitals for name in selected_elements}
        projections = bands.get_projections_on_elements_and_orbitals(projection_spec)
        reference_value = float(getattr(bands, "efermi", 0.0)) if reference.lower() == "fermi" else 0.0
        fig, ax = plt.subplots(figsize=(7.2, 5.2))
        labels = [f"{name}-{orb}" for name in selected_elements for orb in selected_orbitals]
        colors = {name: color for name, color in zip(labels, ("tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple", "tab:brown"))}
        for spin, energies in bands.bands.items():
            spin_projection = projections.get(spin, [])
            for band_index, band in enumerate(energies):
                for point_index, energy in enumerate(band):
                    point = spin_projection[band_index][point_index]
                    for name in selected_elements:
                        for orb in selected_orbitals:
                            label = f"{name}-{orb}"
                            weight = max(float(point.get(name, {}).get(orb, 0.0)), 0.0)
                            if not weight:
                                continue
                            ax.scatter(
                                bands.distance[point_index], float(energy) - reference_value,
                                s=8.0 + 90.0 * min(weight, 1.0),
                                color=colors[label], alpha=0.55, linewidths=0,
                            )
        ticks, labels = _label_ticks(bands)
        if ticks:
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels)
            for tick in ticks:
                ax.axvline(tick, color="0.75", linewidth=0.6)
        ax.axhline(0.0, color="black", linestyle="--", linewidth=0.8)
        ax.set_xlabel("High-symmetry k path")
        ax.set_ylabel(f"Energy - {reference.title()} (eV)")
        ax.set_title("VASP projected band structure")
        for name, color in colors.items():
            ax.scatter([], [], s=50, color=color, label=name)
        ax.legend(title="Projection", loc="best")
        suffix = f"_{element}_{orbital}" if element and orbital else f"_{element}" if element else f"_{orbital}" if orbital else "_elements_orbitals"
        return _save(fig, self.output_dir / f"vasp_projected_bands{suffix}.png")

    def plot_dos(self, *, reference: str = "fermi") -> str:
        plt = _load_matplotlib()
        dos = self.vasprun.complete_dos
        if dos is None:
            raise RuntimeError("vasprun.xml does not contain DOS data.")
        reference_value = float(dos.efermi) if reference.lower() == "fermi" else 0.0
        fig, ax = plt.subplots(figsize=(6.4, 5.0))
        for spin, density in dos.densities.items():
            label = getattr(spin, "name", str(spin))
            ax.plot(dos.energies - reference_value, density, label=label)
        ax.axvline(0.0, color="tab:red", linestyle="--", linewidth=0.8)
        ax.set_xlabel(f"Energy - {reference.title()} (eV)")
        ax.set_ylabel("DOS (states/eV)")
        ax.set_title("VASP total density of states")
        ax.legend()
        return _save(fig, self.output_dir / "vasp_dos.png")

    def plot_projected_dos(
        self, *, element: str | None = None, orbital: str | None = None, reference: str = "fermi"
    ) -> str:
        plt = _load_matplotlib()
        dos = self.vasprun.complete_dos
        if dos is None:
            raise RuntimeError("vasprun.xml does not contain DOS data.")
        reference_value = float(dos.efermi) if reference.lower() == "fermi" else 0.0
        fig, ax = plt.subplots(figsize=(6.4, 5.0))
        series: list[tuple[str, Any]] = []
        if element:
            element_dos = dos.get_element_dos().get(element)
            if element_dos is None:
                raise ValueError(f"No projected DOS was found for element {element!r}.")
            for spin, density in element_dos.densities.items():
                series.append((f"{element} {getattr(spin, 'name', spin)}", density))
        elif orbital:
            orbital_dos = dos.get_spd_dos()
            selected = next((key for key in orbital_dos if str(key).lower() == orbital.lower()), None)
            if selected is None:
                raise ValueError(f"No projected DOS was found for orbital {orbital!r}.")
            for spin, density in orbital_dos[selected].densities.items():
                series.append((f"{selected} {getattr(spin, 'name', spin)}", density))
        else:
            for key, partial in dos.get_spd_dos().items():
                for spin, density in partial.densities.items():
                    series.append((f"{key} {getattr(spin, 'name', spin)}", density))
        for label, density in series:
            ax.plot(dos.energies - reference_value, density, label=label)
        ax.axvline(0.0, color="tab:red", linestyle="--", linewidth=0.8)
        ax.set_xlabel(f"Energy - {reference.title()} (eV)")
        ax.set_ylabel("Projected DOS (states/eV)")
        ax.set_title("VASP projected density of states")
        ax.legend(fontsize=8, ncol=2)
        suffix = f"_{element}" if element else f"_{orbital}" if orbital else "_spd"
        return _save(fig, self.output_dir / f"vasp_projected_dos{suffix}.png")

    def plot_all(self, *, element: str | None = None, orbital: str | None = None) -> list[str]:
        outputs = [self.plot_band_structure(), self.plot_dos()]
        try:
            outputs.append(self.plot_projected_band_structure(element=element, orbital=orbital))
        except (RuntimeError, ValueError):
            pass
        try:
            outputs.append(self.plot_projected_dos(element=element, orbital=orbital))
        except (RuntimeError, ValueError):
            pass
        return outputs


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plot common VASP electronic-structure outputs.")
    parser.add_argument("run_dir", help="Completed VASP directory containing vasprun.xml")
    parser.add_argument("--output-dir", default="", help="Directory for generated plots")
    parser.add_argument("--plot", choices=("all", "bands", "projected-bands", "dos", "pdos"), default="all")
    parser.add_argument("--element", default="", help="Element filter for projected bands or DOS")
    parser.add_argument("--orbital", default="", help="Orbital filter for projected bands/DOS, e.g. s, p, d")
    args = parser.parse_args(list(argv) if argv is not None else None)
    plotter = VASPPlotter(args.run_dir, args.output_dir or None)
    element = args.element or None
    orbital = args.orbital or None
    methods = {
        "bands": lambda: [plotter.plot_band_structure()],
        "projected-bands": lambda: [plotter.plot_projected_band_structure(element=element, orbital=orbital)],
        "dos": lambda: [plotter.plot_dos()],
        "pdos": lambda: [plotter.plot_projected_dos(element=element, orbital=orbital)],
        "all": lambda: plotter.plot_all(element=element, orbital=orbital),
    }
    outputs = methods[args.plot]()
    manifest = {"run_dir": str(plotter.run_dir), "plots": outputs}
    manifest_path = plotter.output_dir / "vasp_plot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
