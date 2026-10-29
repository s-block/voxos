import json
import secrets
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
GITLEAKS = shutil.which("gitleaks")


@unittest.skipUnless(GITLEAKS, "Gitleaks is required")
class SecretScanningTests(unittest.TestCase):
    def _scan(self, directory: Path) -> subprocess.CompletedProcess[str]:
        assert GITLEAKS is not None
        return subprocess.run(  # noqa: S603 - resolved scanner, fixed config, isolated test directory
            [GITLEAKS, "dir", str(directory), "--config", str(ROOT / ".gitleaks.toml"), "--redact=100", "--no-banner"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_known_fixture_paths_cannot_hide_new_credentials(self) -> None:
        for filename in [".env.example", ".secrets.baseline", "app/ios/Podfile.lock"]:
            with self.subTest(filename=filename), TemporaryDirectory() as temporary:
                directory = Path(temporary)
                path = directory / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                credential = secrets.token_hex(32)
                path.write_text(f'api_key = "{credential}"\n')
                result = self._scan(directory)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn(credential, result.stdout + result.stderr)

    def test_detection_fingerprints_do_not_require_plaintext_exemptions(self) -> None:
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / ".secrets.baseline").write_text(json.dumps({"hashed_secret": secrets.token_hex(20)}, indent=2))
            self.assertEqual(self._scan(directory).returncode, 0)
