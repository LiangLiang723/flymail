import unittest
from pathlib import Path


class DockerEntrypointTest(unittest.TestCase):
    def test_mysql_binary_logging_is_disabled(self):
        entrypoint = Path(__file__).resolve().parents[2] / "scripts" / "docker-entrypoint.sh"
        script = entrypoint.read_text(encoding="utf-8")

        self.assertIn("--skip-log-bin", script)


if __name__ == "__main__":
    unittest.main()
