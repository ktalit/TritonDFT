import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vasp_plotter import VASPPlotter, _label_ticks


class FakeVasprun:
    def __init__(self):
        self.complete_dos = None

    def get_band_structure(self, line_mode=True):
        self.called_line_mode = line_mode
        return SimpleNamespace()


class VASPPlotterTests(unittest.TestCase):
    def test_band_ticks_use_branch_indices_for_pymatgen_kpoints(self):
        fake_kpoint = object()
        bands = SimpleNamespace(
            distance=[0.0, 0.5, 1.0],
            branches=[
                {"start_index": 0, "end_index": 1, "name": "Gamma-X"},
                {"start_index": 1, "end_index": 2, "name": "X-L"},
            ],
            labels_dict={"Gamma": fake_kpoint},
        )

        self.assertEqual(_label_ticks(bands), ([0.0, 0.5, 1.0], ["Gamma", "X", "L"]))

    def test_band_ticks_use_and_clean_labeled_kpoints(self):
        bands = SimpleNamespace(
            distance=[0.0, 0.5, 1.0],
            kpoints=[
                SimpleNamespace(label="10 ! Gamma"),
                SimpleNamespace(label="10 ! X"),
                SimpleNamespace(label="10 ! M"),
            ],
        )

        self.assertEqual(_label_ticks(bands), ([0.0, 0.5, 1.0], ["Gamma", "X", "M"]))

    def test_requires_vasprun_for_vasp_plots(self):
        with tempfile.TemporaryDirectory() as tmp:
            plotter = VASPPlotter(tmp)
            with self.assertRaises(FileNotFoundError):
                plotter.plot_band_structure()

    def test_output_directory_is_separate_from_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plots"
            plotter = VASPPlotter(tmp, output)
            self.assertEqual(plotter.output_dir, output.resolve())

    def test_band_plot_uses_vasprun_band_data(self):
        fake_bands = SimpleNamespace(
            efermi=1.5,
            distance=[0.0, 1.0],
            bands={"up": [[1.5, 2.5]]},
            labels_dict={"Gamma": 0.0, "X": 1.0},
        )
        with tempfile.TemporaryDirectory() as tmp:
            vasprun = Path(tmp) / "vasprun.xml"
            vasprun.write_text("placeholder", encoding="utf-8")
            plotter = VASPPlotter(tmp)
            with patch("vasp_plotter._load_vasprun") as loader:
                loader.return_value = SimpleNamespace(
                    get_band_structure=lambda line_mode=True: fake_bands
                )
                output = plotter.plot_band_structure()
            self.assertTrue(Path(output).is_file())
            self.assertGreater(Path(output).stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
