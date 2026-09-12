import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.vasp_agent import (
    RemoteClusterVASPAgent,
    VASP_RELAXED_POSCAR_PLACEHOLDER,
    VASPInputSet,
    _apply_default_vasp_relaxation,
    _derive_downstream_incar,
    _enforce_vasp_workflow_incar,
    _is_relaxed_poscar_placeholder,
    _preferred_vasp_poscar,
    _step_directory_name,
)
from src.execute_code.slurm_template import render_slurm_script


class VASPStepDirectoryNamingTests(unittest.TestCase):
    def test_vc_relax_is_default_unless_user_opts_out(self):
        bands = [{"title": "SCF", "task": "scf"}, {"title": "Bands", "task": "bands"}]
        default = _apply_default_vasp_relaxation("calculate Si bands", bands)
        self.assertEqual([step["task"] for step in default], ["vc-relax", "scf", "bands"])

        skipped = _apply_default_vasp_relaxation("calculate Si bands without relaxation", bands)
        self.assertEqual([step["task"] for step in skipped], ["scf", "bands"])

        constrained = _apply_default_vasp_relaxation("calculate Si bands with fixed-cell relaxation", bands)
        self.assertEqual([step["task"] for step in constrained], ["relax", "scf", "bands"])

        existing = _apply_default_vasp_relaxation(
            "relax and calculate bands", [{"title": "Relax", "task": "vc-relax"}, *bands]
        )
        self.assertEqual([step["task"] for step in existing], ["vc-relax", "scf", "bands"])

    def test_semantic_step_names_are_used_for_vasp_runs(self):
        self.assertEqual(_step_directory_name("vc-relax", 1), "vc-relax")
        self.assertEqual(_step_directory_name("scf", 1), "scf")
        self.assertEqual(_step_directory_name("dos", 1), "dos")
        self.assertEqual(_step_directory_name("bands", 1), "bands")

    def test_duplicate_task_names_are_disambiguated(self):
        self.assertEqual(_step_directory_name("scf", 1), "scf")
        self.assertEqual(_step_directory_name("scf", 2), "scf_2")
        self.assertEqual(_step_directory_name("relax", 3), "relax_3")

    def test_vc_relax_and_band_incar_dependencies_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            incar = Path(tmp) / "INCAR"
            incar.write_text("ISIF = 2\nNSW = 0\n", encoding="utf-8")
            _enforce_vasp_workflow_incar(incar, "vc-relax")
            relax_text = incar.read_text(encoding="utf-8")
            self.assertIn("ISIF = 3", relax_text)
            self.assertIn("IBRION = 2", relax_text)
            self.assertIn("NSW = 100", relax_text)

            incar.write_text("ICHARG = 2\nNSW = 50\n", encoding="utf-8")
            _enforce_vasp_workflow_incar(incar, "bands")
            bands_text = incar.read_text(encoding="utf-8")
            self.assertIn("ICHARG = 11", bands_text)
            self.assertIn("NSW = 0", bands_text)
            self.assertIn("LCHARG = .FALSE.", bands_text)

    def test_downstream_poscar_placeholder_is_not_a_generated_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            poscar = Path(tmp) / "POSCAR"
            poscar.write_text(VASP_RELAXED_POSCAR_PLACEHOLDER, encoding="utf-8")
            self.assertTrue(_is_relaxed_poscar_placeholder(poscar))
            self.assertNotIn("Direct", poscar.read_text(encoding="utf-8"))

    def test_downstream_incar_inherits_relaxation_physics(self):
        relax = (
            "SYSTEM = Si\nENCUT = 520\nGGA = PE\nPREC = Accurate\n"
            "ISPIN = 2\nISIF = 3\nIBRION = 2\nNSW = 100\nEDIFFG = -0.02\n"
        )
        independently_generated = (
            "ENCUT = 300\nGGA = 91\nPREC = Low\nISPIN = 1\n"
            "NBANDS = 24\nICHARG = 2\n"
        )
        derived = _derive_downstream_incar(relax, independently_generated, "bands")
        self.assertIn("ENCUT = 520", derived)
        self.assertIn("GGA = PE", derived)
        self.assertIn("PREC = Accurate", derived)
        self.assertIn("ISPIN = 2", derived)
        self.assertIn("NBANDS = 24", derived)
        self.assertNotIn("ENCUT = 300", derived)
        self.assertNotIn("ISIF", derived)

    def test_materials_project_primitive_cell_is_preferred(self):
        class FakeStructure:
            def __init__(self, label):
                self.label = label

            def to(self, *, fmt):
                self.asserted_format = fmt
                return self.label

        primitive = FakeStructure("primitive POSCAR")
        conventional = FakeStructure("conventional POSCAR")
        info = {
            "primitive_structure": [primitive],
            "conventional_structure": [conventional],
        }
        self.assertEqual(_preferred_vasp_poscar("calculate bands", info), "primitive POSCAR\n")
        self.assertEqual(
            _preferred_vasp_poscar("use the conventional cell", info),
            "conventional POSCAR\n",
        )

    def test_execution_materializes_one_qe_style_directory_per_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            approved = root / "relax"
            approved.mkdir()
            files = []
            for name in ("POSCAR", "INCAR", "KPOINTS", "POTCAR"):
                path = approved / name
                path.write_text(name + "\n", encoding="utf-8")
                files.append(str(path))
            item = VASPInputSet(
                step_index=1,
                title="Relax",
                task="vc-relax",
                directory=approved,
                files=files,
                species=["Si"],
                output_path=str(approved / "vasp.out"),
            )
            remote = RemoteClusterVASPAgent.__new__(RemoteClusterVASPAgent)
            remote.agent = SimpleNamespace(work_dir=root)

            jobs = remote._materialize_job_directories([item])

            job_dir = root / "attempts" / "01-vc-relax" / "attempt_001"
            self.assertEqual(jobs[0].directory, job_dir)
            self.assertEqual(Path(jobs[0].output_path), job_dir / "vasp.out")
            self.assertTrue(all((job_dir / name).is_file() for name in ("POSCAR", "INCAR", "KPOINTS", "POTCAR")))
            self.assertEqual((approved / "POSCAR").read_text(encoding="utf-8"), "POSCAR\n")

    def test_vasp_template_preserves_intel_mpi_launcher_and_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "vasp.slurm"
            template.write_text(
                "#!/bin/bash\n"
                "#SBATCH --nodes=1\n"
                "#SBATCH --ntasks-per-node=32\n"
                "#SBATCH --time=00:30:00\n"
                "#SBATCH --output=vasp.%j.out\n"
                "#SBATCH --error=vasp.%j.err\n"
                "module load intel-mpi\n"
                "export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK\n"
                "mpirun -genv I_MPI_PIN_DOMAIN=omp:compact vasp_std > vasp.log\n",
                encoding="utf-8",
            )
            rendered = render_slurm_script(
                exec_path="vasp_ncl",
                input_path="POSCAR",
                output_path="vasp.out",
                command_line="mpirun -np 1 $exe > $OUTPUT",
                nodes=1,
                tasks_per_node=1,
                work_dir=".",
                template_path=str(template),
                preserve_template_launcher_options=True,
                preserve_template_resources=True,
                preserve_template_logs=True,
            )
            self.assertIn("#SBATCH --ntasks-per-node=32", rendered)
            self.assertIn("#SBATCH --output=vasp.%j.out", rendered)
            self.assertIn("mpirun -genv I_MPI_PIN_DOMAIN=omp:compact $exe > $OUTPUT", rendered)
            self.assertEqual(rendered.count("mpirun "), 1)
            self.assertIn("exe=vasp_ncl", rendered)


if __name__ == "__main__":
    unittest.main()
