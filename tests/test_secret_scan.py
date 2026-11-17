from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "secret_scan", Path(__file__).resolve().parent.parent / "scripts" / "secret_scan.py"
)
assert _SPEC and _SPEC.loader
secret_scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(secret_scan)


class SecretScanTests(unittest.TestCase):
    def _scan(self, files: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(content, encoding="utf-8")
            return secret_scan.scan(root, list(files))

    def test_tracked_env_file_is_rejected(self) -> None:
        findings = self._scan({".env": "TRADING_MODE=paper\n"})
        self.assertEqual(len(findings), 1)
        self.assertIn("forbidden", findings[0])

    def test_env_example_with_placeholders_is_allowed(self) -> None:
        self.assertEqual(self._scan({".env.example": "PAPER_BROKER_API_KEY=\n"}), [])

    def test_private_key_and_api_key_assignment_are_detected(self) -> None:
        findings = self._scan(
            {
                "a.py": "-----BEGIN RSA PRIVATE KEY-----\n",
                "b.py": 'api_key = "abcdefghijklmnopqrstuvwxyz123456"\n',
            }
        )
        self.assertEqual(len(findings), 2)

    def test_database_files_are_rejected(self) -> None:
        findings = self._scan({"state/research.db": "x"})
        self.assertTrue(findings)

    def test_clean_source_passes(self) -> None:
        self.assertEqual(self._scan({"ok.py": "risk_per_trade = 0.002\n"}), [])


if __name__ == "__main__":
    unittest.main()
