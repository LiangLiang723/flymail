from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPOSITORY_ROOT / "scripts" / "docker-entrypoint.sh"
DOCKERFILE = REPOSITORY_ROOT / "Dockerfile"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class DockerEntrypointContractTest(unittest.TestCase):
    def test_static_contract_supervises_mysql_worker_and_api(self):
        script = ENTRYPOINT.read_text(encoding="utf-8")

        self.assertIn("--skip-log-bin", script)
        self.assertIn("--bind-address=127.0.0.1", script)
        self.assertIn("migrate.py", script)
        self.assertIn("worker.py", script)
        self.assertIn("-m uvicorn main:app", script)
        self.assertIn("wait_for_worker_heartbeat", script)
        self.assertIn('chmod o+x "${DATA_ROOT}"', script)
        self.assertIn("worker_pid", script)
        self.assertIn("api_pid", script)
        self.assertIn("wait -n", script)
        self.assertLess(script.index("migrate.py"), script.index("worker.py"))
        self.assertLess(script.index("worker.py"), script.index("-m uvicorn main:app"))

        shutdown = script.index("shutdown_services()")
        shutdown_body = script[shutdown : script.index("mkdir -p", shutdown)]
        self.assertLess(shutdown_body.index("api_pid"), shutdown_body.index("worker_pid"))
        self.assertLess(shutdown_body.index("worker_pid"), shutdown_body.index("mysql_pid"))
        self.assertNotIn('echo "${MYSQL_PASSWORD}', script)
        self.assertNotIn('echo "${FLYMAIL_SESSION_SECRET}', script)

    def test_dockerfile_copies_only_v2_runtime_and_uses_v2_health(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("/api/health", dockerfile)
        self.assertNotIn("ENV MYSQL_PASSWORD", dockerfile)
        self.assertNotIn("FLYMAIL_SESSION_SECRET=", dockerfile)
        self.assertNotIn("COPY backend/ /app/backend/", dockerfile)
        self.assertIn("COPY backend/flymail/ /app/backend/flymail/", dockerfile)
        self.assertIn("backend/main.py", dockerfile)
        self.assertIn("backend/worker.py", dockerfile)
        self.assertIn("backend/migrate.py", dockerfile)


class DockerEntrypointStubIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="flymail-entrypoint-test-")
        self.root = Path(self.temp.name)
        self.bin_dir = self.root / "bin"
        self.data_dir = self.root / "data"
        self.mysql_dir = self.data_dir / "mysql"
        self.mysql_dir.joinpath("mysql").mkdir(parents=True)
        self.bin_dir.mkdir()
        self.events = self.root / "events.log"
        self.mysql_pid_file = self.root / "mysqld.pid"
        self._write_stubs()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_stubs(self) -> None:
        _write_executable(
            self.bin_dir / "mysqld",
            r"""
            #!/usr/bin/env bash
            set -eu
            printf '%s\n' 'mysql-start' >>"${STUB_EVENT_FILE}"
            printf '%s\n' "$$" >"${STUB_MYSQL_PID_FILE}"
            trap 'printf "%s\n" mysql-term >>"${STUB_EVENT_FILE}"; exit 0' TERM INT
            while :; do sleep 0.05; done
            """,
        )
        _write_executable(
            self.bin_dir / "mysqladmin",
            r"""
            #!/usr/bin/env bash
            set -eu
            case " $* " in
              *" ping "*) exit 0 ;;
              *" shutdown "*)
                printf '%s\n' 'mysql-shutdown' >>"${STUB_EVENT_FILE}"
                kill -TERM "$(cat "${STUB_MYSQL_PID_FILE}")" 2>/dev/null || true
                exit 0
                ;;
            esac
            exit 0
            """,
        )
        _write_executable(
            self.bin_dir / "mysql",
            r"""
            #!/usr/bin/env bash
            set -eu
            case " $* " in
              *"process_heartbeats"*) printf '%s\n' '1'; exit 0 ;;
            esac
            cat >/dev/null || true
            printf '%s\n' 'mysql-sql' >>"${STUB_EVENT_FILE}"
            """,
        )
        _write_executable(
            self.bin_dir / "flymail-python",
            r"""
            #!/usr/bin/env bash
            set -eu
            event() { printf '%s\n' "$1" >>"${STUB_EVENT_FILE}"; }
            case " $* " in
              *" migrate.py "*|*" migrate.py")
                event migration
                exit "${STUB_MIGRATION_EXIT_CODE:-0}"
                ;;
              *" worker.py "*|*" worker.py")
                event worker-start
                trap 'event worker-term; exit 0' TERM INT
                if [[ -n "${STUB_WORKER_EXIT_CODE:-}" ]]; then
                  sleep 0.20
                  exit "${STUB_WORKER_EXIT_CODE}"
                fi
                while :; do sleep 0.05; done
                ;;
              *" -m uvicorn main:app "*)
                event api-start
                trap 'event api-term; exit 0' TERM INT
                if [[ -n "${STUB_API_EXIT_CODE:-}" ]]; then
                  sleep 0.20
                  exit "${STUB_API_EXIT_CODE}"
                fi
                while :; do sleep 0.05; done
                ;;
            esac
            event "unexpected-python:$*"
            exit 97
            """,
        )

    def _environment(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env.get('PATH', '')}",
                "FLYMAIL_ENTRYPOINT_TEST_MODE": "1",
                "FLYMAIL_STORAGE_ROOT": str(self.data_dir),
                "FLYMAIL_DATA_DIR": str(self.data_dir / "flymail"),
                "FLYMAIL_SESSION_SECRET": "entrypoint-test-session-secret",
                "MYSQL_DATABASE": "flymail_test",
                "MYSQL_USER": "flymail_test",
                "MYSQL_PASSWORD": "quote'\\@:/%password",
                "FLYMAIL_PYTHON_BIN": str(self.bin_dir / "flymail-python"),
                "STUB_EVENT_FILE": str(self.events),
                "STUB_MYSQL_PID_FILE": str(self.mysql_pid_file),
                "FLYMAIL_WORKER_READY_TIMEOUT": "3",
                "FLYMAIL_SHUTDOWN_TIMEOUT": "2",
            }
        )
        env.update(overrides)
        return env

    def _run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ENTRYPOINT)],
            cwd=REPOSITORY_ROOT,
            env=self._environment(**overrides),
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )

    def _events(self) -> list[str]:
        if not self.events.exists():
            return []
        return self.events.read_text(encoding="utf-8").splitlines()

    def test_migration_failure_prevents_worker_and_api_start(self):
        result = self._run(STUB_MIGRATION_EXIT_CODE="6")

        self.assertEqual(result.returncode, 6, result.stderr)
        events = self._events()
        self.assertIn("migration", events)
        self.assertNotIn("worker-start", events)
        self.assertNotIn("api-start", events)
        self.assertIn("mysql-shutdown", events)

    def test_worker_exit_stops_api_and_mysql_with_worker_status(self):
        result = self._run(STUB_WORKER_EXIT_CODE="7")

        self.assertEqual(result.returncode, 7, result.stderr)
        events = self._events()
        self.assertLess(events.index("worker-start"), events.index("api-start"))
        self.assertIn("api-term", events)
        self.assertIn("mysql-shutdown", events)
        self.assertLess(events.index("api-term"), events.index("mysql-shutdown"))

    def test_api_exit_stops_worker_and_mysql_with_api_status(self):
        result = self._run(STUB_API_EXIT_CODE="9")

        self.assertEqual(result.returncode, 9, result.stderr)
        events = self._events()
        self.assertIn("worker-term", events)
        self.assertIn("mysql-shutdown", events)
        self.assertLess(events.index("worker-term"), events.index("mysql-shutdown"))

    def test_sigterm_drains_api_then_worker_then_mysql(self):
        process = subprocess.Popen(
            ["bash", str(ENTRYPOINT)],
            cwd=REPOSITORY_ROOT,
            env=self._environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if "api-start" in self._events():
                break
            if process.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNone(process.poll())

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 143, f"{stdout}\n{stderr}")
        events = self._events()
        self.assertLess(events.index("api-term"), events.index("worker-term"))
        self.assertLess(events.index("worker-term"), events.index("mysql-shutdown"))


if __name__ == "__main__":
    unittest.main()
