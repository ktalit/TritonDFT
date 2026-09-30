import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


class TritonDFTCliTests(unittest.TestCase):
    def test_init_is_idempotent_and_doctor_reports_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"TRITONDFT_HOME": tmp}, clear=False):
                import tritondft_cli

                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(tritondft_cli.init_command(), 0)
                    self.assertEqual(tritondft_cli.init_command(), 0)
                config = Path(tmp) / "config.yaml"
                self.assertTrue(config.is_file())
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                self.assertTrue((Path(tmp) / "slurm" / "my-cluster-qe.sh").is_file())
                with redirect_stdout(StringIO()):
                    self.assertEqual(tritondft_cli.doctor_command(), 1)

    def test_version_is_available_without_an_installed_distribution(self):
        import tritondft_cli

        self.assertRegex(tritondft_cli._version(), r"^\d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
