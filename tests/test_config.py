import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from detect_app.config import AppPaths


class ConfigTests(unittest.TestCase):
    def test_local_appdata_and_image_folder_are_configurable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local_app_data = root / "local"
            image_folder = root / "user-images"
            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": str(local_app_data),
                    "DETECT_APP_IMAGES": str(image_folder),
                },
            ):
                paths = AppPaths.create()
            self.assertEqual(paths.database, local_app_data / "DataPeanuts" / "detect_app" / "database" / "detect_app.sqlite3")
            self.assertEqual(paths.images, image_folder)
            self.assertTrue(paths.models.is_dir())

    def test_new_storage_is_independent_and_preserves_legacy_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local_app_data = root / "local"
            legacy_database = local_app_data / "DataPeanuts" / "NutsVision" / "database" / "nuts_vision.sqlite3"
            legacy_database.parent.mkdir(parents=True)
            legacy_database.write_bytes(b"legacy-history")
            image_folder = root / "selected-images"
            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": str(local_app_data),
                    "DETECT_APP_IMAGES": str(image_folder),
                },
            ):
                paths = AppPaths.create()

            self.assertEqual(paths.database, local_app_data / "DataPeanuts" / "detect_app" / "database" / "detect_app.sqlite3")
            self.assertEqual(legacy_database.read_bytes(), b"legacy-history")
            self.assertFalse(paths.database.exists())
            self.assertNotEqual(paths.images, legacy_database.parent)

    def test_resources_resolve_from_outside_the_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": temporary, "DETECT_APP_IMAGES": str(Path(temporary) / "images")},
            ):
                previous = Path.cwd()
                try:
                    os.chdir(temporary)
                    paths = AppPaths.create()
                finally:
                    os.chdir(previous)
            self.assertTrue(paths.bundled_model.is_file())
            self.assertTrue(paths.icon.is_file())

    def test_relative_image_environment_path_is_anchored_to_user_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            with (
                patch.object(Path, "home", return_value=home),
                patch.dict(
                    os.environ,
                    {"LOCALAPPDATA": str(root / "local"), "DETECT_APP_IMAGES": "chosen-images"},
                ),
            ):
                paths = AppPaths.create()
            self.assertEqual(paths.images, home / "chosen-images")


if __name__ == "__main__":
    unittest.main()
