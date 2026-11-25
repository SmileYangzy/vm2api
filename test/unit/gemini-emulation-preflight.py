import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "scripts/gemini-host.py"
spec = importlib.util.spec_from_file_location("gemini_host", source)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class EmulationPreflight(unittest.TestCase):
    def check(self, handler="enabled\nflags: POCF", loader=True, returncode=0,
              payload=None, license_code="license_required"):
        def probe(command, **kwargs):
            stdout = ""
            if "--license-status" in command:
                stdout = json.dumps({"valid": False, "code": license_code})
            elif command[-2:] == ["/opt/agy", "--version"]:
                stdout = "1.5.9"
            return subprocess.CompletedProcess(command, returncode, stdout, "uid map denied")

        with (
            patch.object(host, "is_arm_host", return_value=True),
            patch.object(host.os, "geteuid", return_value=0),
            patch.object(Path, "read_text", return_value=handler),
            patch.object(host.os, "access", return_value=loader),
            patch.object(host.os.path, "isfile", return_value=loader),
            patch.object(host, "check_root_owned"),
            patch.object(host.subprocess, "run", side_effect=probe) as run,
        ):
            host.check_amd64_emulation(29000, payload)
            return run.call_args_list

    def test_namespace_and_loader_are_exercised_as_the_sandbox_uid(self):
        calls = self.check()
        self.assertEqual(len(calls), 2)
        command = calls[-1].args[0]
        self.assertEqual(command[:4], ["unshare", "--net", "--", "setpriv"])
        self.assertEqual(command[command.index("--reuid") + 1], "29000")
        self.assertIn("--bounding-set=-all", command)
        self.assertIn("--unshare-pid", command)
        self.assertEqual(command[-2:], ["/lib64/ld-linux-x86-64.so.2", "--version"])

    def test_handler_missing_fixed_interpreter_flag(self):
        with self.assertRaisesRegex(RuntimeError, "F flag"):
            self.check(handler="enabled\nflags: POC")

    def test_worker_checks_a_private_empty_license_directory(self):
        worker = self.check(payload=Path("/test-payload"))[-1].args[0]
        license_dir = worker[worker.index("--license-dir") + 1]
        creation = worker.index(license_dir)
        self.assertEqual(worker[creation - 3:creation], ["--perms", "0700", "--dir"])
        self.assertIn("--license-status", worker)

    def test_invalid_status_is_not_an_empty_license_success(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected empty-license status"):
            self.check(payload=Path("/test-payload"), license_code="license_invalid")

    def test_missing_loader_and_denied_sandbox_are_both_reported(self):
        with self.assertRaisesRegex(RuntimeError, "missing host amd64 loader.*uid map denied"):
            self.check(loader=False, returncode=1)


if __name__ == "__main__":
    unittest.main()
