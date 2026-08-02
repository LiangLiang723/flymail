from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class FormalRuntimeEntryTests(unittest.TestCase):
    def test_formal_main_exports_v2_app_without_legacy_runtime_imports(self):
        main_path = BACKEND_ROOT / "main.py"
        source = main_path.read_text(encoding="utf-8")
        self.assertIn("from flymail.api.app import create_app", source)
        self.assertIn("FlyMailSettings.from_env(\"api\")", source)
        for forbidden in (
            "routes.messages",
            "routes.accounts",
            "routes.compose",
            "services.scheduler",
            "legacy",
        ):
            self.assertNotIn(forbidden, source)

        with tempfile.TemporaryDirectory(prefix="flymail-runtime-entry-") as value:
            data_dir = Path(value)
            env = {
                "DATABASE_URL": "mysql://flymail:redacted@127.0.0.1:3306/flymail_runtime",
                "FLYMAIL_DATA_DIR": str(data_dir),
                "FLYMAIL_SESSION_SECRET": "formal-runtime-session-secret-0123456789",
            }
            module_name = "flymail_formal_main_test"
            specification = importlib.util.spec_from_file_location(module_name, main_path)
            self.assertIsNotNone(specification)
            self.assertIsNotNone(specification.loader)
            module = importlib.util.module_from_spec(specification)
            with patch.dict(os.environ, env, clear=False):
                sys.modules[module_name] = module
                try:
                    specification.loader.exec_module(module)
                finally:
                    sys.modules.pop(module_name, None)
        self.assertEqual(module.app.title, "FlyMail V2")
        self.assertIn("/api/health", module.app.openapi()["paths"])

    def test_formal_worker_wrapper_uses_packaged_runtime(self):
        wrapper = (BACKEND_ROOT / "worker.py").read_text(encoding="utf-8")
        runtime = (BACKEND_ROOT / "flymail" / "workers" / "main.py").read_text(encoding="utf-8")
        self.assertIn("from flymail.workers.main import main", wrapper)
        self.assertNotIn("v2_worker", wrapper)
        self.assertIn("async def run_worker", runtime)
        self.assertIn("loop.add_signal_handler", runtime)
        self.assertIn("stop.set()", runtime)
        self.assertIn("_release_worker_leases", runtime)
        self.assertIn("await pool.close()", runtime)
        self.assertNotIn("from " + "v2_worker", runtime)

    def test_packaged_worker_exports_async_lifecycle(self):
        from flymail.workers import main as runtime

        self.assertTrue(inspect.iscoroutinefunction(runtime.run_worker))
        self.assertTrue(callable(runtime.main))
        self.assertGreater(len(runtime.WORKER_JOB_KINDS), 10)

    def test_development_entries_are_not_runtime_dependencies(self):
        runtime_files = (
            BACKEND_ROOT / "main.py",
            BACKEND_ROOT / "worker.py",
        )
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)
        for forbidden in ("v2_dev", "v2_worker", "v2-main", "v2.html"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
