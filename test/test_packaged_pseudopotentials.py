import tempfile
import unittest
from pathlib import Path

from config import Config
from tritondft_data.pseudopotentials import packaged_pseudo_dir, packaged_pseudodojo_root
from utils import patch_qe_input_file, validate_pseudos_exist


class PackagedPseudopotentialTests(unittest.TestCase):
    def test_all_default_libraries_resolve_inside_packaged_data(self):
        root = packaged_pseudodojo_root()
        config = Config.load("this-config-does-not-exist.yaml")

        for family, directory in config.pseudo.as_dict().items():
            path = Path(directory)
            self.assertTrue(path.is_relative_to(root), family)
            self.assertTrue(any(path.glob("*.upf")), family)

    def test_explicit_pseudopotential_override_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom = root / "custom-pseudos"
            custom.mkdir()
            config_file = root / "custom.yaml"
            config_file.write_text(f"pseudo:\n  LDA: {custom}\n", encoding="utf-8")

            config = Config.load(str(config_file))

        self.assertEqual(config.pseudo.LDA, str(custom))
        self.assertEqual(config.pseudo.PBE, str(packaged_pseudo_dir("PBE")))

    def test_si_lda_input_generation_uses_packaged_pseudopotential(self):
        pseudo_dir = packaged_pseudo_dir("LDA")
        self.assertTrue((pseudo_dir / "si.upf").is_file())

        qe_input = """&control
 calculation = 'relax',
 pseudo_dir = './missing',
/
&system
 ibrav = 2,
 nat = 2,
 ntyp = 1,
/
ATOMIC_SPECIES
Si 28.0855 si.upf
ATOMIC_POSITIONS crystal
Si 0.0 0.0 0.0
Si 0.25 0.25 0.25
K_POINTS automatic
4 4 4 0 0 0
"""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "si-lda-relax.in"
            input_path.write_text(qe_input, encoding="utf-8")
            patch_qe_input_file(str(input_path), new_pseudo_dir=str(pseudo_dir))
            _, error = validate_pseudos_exist(str(input_path))
            generated = input_path.read_text(encoding="utf-8")

        self.assertIsNone(error)
        self.assertIn(f"pseudo_dir = '{pseudo_dir}'", generated)
        self.assertIn("Si 28.0855 si.upf", generated)


if __name__ == "__main__":
    unittest.main()
