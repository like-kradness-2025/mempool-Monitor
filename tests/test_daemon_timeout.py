"""Regression test: collect_loop_daemon.collect_once survives TimeoutExpired.

The daemon script lives under scripts/ and is not a package, so it is imported
via importlib from its file path.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "collect_loop_daemon", SCRIPTS / "collect_loop_daemon.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["collect_loop_daemon"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class DaemonTimeoutTest(unittest.TestCase):
    def test_collect_once_returns_nonzero_on_timeout(self):
        mod = _load_module()
        timeout_err = subprocess.TimeoutExpired(cmd="mempool_monitor.cli", timeout=45)
        with mock.patch.object(mod, "_log") as mock_log, \
             mock.patch.object(mod.subprocess, "run", side_effect=timeout_err):
            rc = mod.collect_once({})
        self.assertNotEqual(rc, 0)
        self.assertEqual(rc, 1)
        mock_log.assert_called_once_with("collect timed out after 45s")


if __name__ == "__main__":
    unittest.main()
