import importlib.metadata
import os
import subprocess
import sys
import sysconfig
import tempfile
import time
import unittest
from pathlib import Path

import detect_app


class PackagingTests(unittest.TestCase):
    def test_python_package_and_installed_command_use_detect_app_identity(self):
        distribution = importlib.metadata.distribution("detect-app")

        package_file = Path(detect_app.__file__)
        self.assertEqual((package_file.parent.name, package_file.name), ("detect_app", "__init__.py"))
        self.assertEqual(distribution.metadata["Name"], "detect-app")
        self.assertTrue(any(entry.name == "detect-app" for entry in distribution.entry_points))
        self.assertFalse(any(entry.name == "nuts-vision" for entry in distribution.entry_points))

    def test_module_entry_point_runs_outside_the_repository(self):
        with tempfile.TemporaryDirectory(prefix="detect_app_launch_") as temporary:
            root = Path(temporary)
            script_name = "detect-app.exe" if os.name == "nt" else "detect-app"
            command = Path(sysconfig.get_path("scripts")) / script_name
            self.assertTrue(command.is_file(), "The installed detect-app command is missing")
            for index, invocation in enumerate(
                ([sys.executable, "-m", "detect_app"], [str(command)])
            ):
                with self.subTest(invocation=invocation):
                    local_app_data = root / f"local-{index}"
                    env = os.environ.copy()
                    env.update(
                        {
                            "LOCALAPPDATA": str(local_app_data),
                            "DETECT_APP_IMAGES": str(root / f"images-{index}"),
                            "QT_QPA_PLATFORM": "offscreen",
                        }
                    )
                    process = subprocess.Popen(
                        invocation,
                        cwd=root,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    database = (
                        local_app_data
                        / "DataPeanuts"
                        / "detect_app"
                        / "database"
                        / "detect_app.sqlite3"
                    )
                    models = local_app_data / "DataPeanuts" / "detect_app" / "models"
                    try:
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            if process.poll() is not None:
                                stdout, stderr = process.communicate()
                                self.fail(
                                    f"{invocation!r} exited early.\nstdout:\n{stdout}\nstderr:\n{stderr}"
                                )
                            if database.is_file() and models.is_dir() and any(models.glob("*.onnx")):
                                time.sleep(0.2)
                                self.assertIsNone(
                                    process.poll(), "Application exited instead of entering its event loop"
                                )
                                break
                            time.sleep(0.05)
                        else:
                            self.fail(f"{invocation!r} did not initialize outside the repository")
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
