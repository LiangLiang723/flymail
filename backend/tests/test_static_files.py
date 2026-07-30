import tempfile
import unittest
from pathlib import Path

from utils.static_files import resolve_ui_file


class StaticFileResolutionTest(unittest.TestCase):
    def test_returns_existing_file_inside_ui_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ui"
            target = root / "assets" / "app.js"
            target.parent.mkdir(parents=True)
            target.write_text("console.log('ok')", encoding="utf-8")

            resolved = resolve_ui_file(root, "assets/app.js")

            self.assertEqual(resolved, target.resolve())

    def test_rejects_parent_traversal_and_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ui"
            root.mkdir()
            outside = Path(tmp) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")

            self.assertIsNone(resolve_ui_file(root, "../secret.txt"))
            self.assertIsNone(resolve_ui_file(root, str(outside)))
            self.assertIsNone(resolve_ui_file(root, "..\\secret.txt"))

    def test_main_lifecycle_starts_and_stops_attachment_cache_maintenance(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn("start_attachment_cache_maintenance()", source)
        self.assertIn("await stop_attachment_cache_maintenance()", source)

    def test_rejects_symlink_that_resolves_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ui"
            root.mkdir()
            outside = Path(tmp) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            link = root / "secret-link"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink is unavailable")

            self.assertIsNone(resolve_ui_file(root, "secret-link"))


if __name__ == "__main__":
    unittest.main()
