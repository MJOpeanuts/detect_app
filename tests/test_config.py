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


if __name__ == "__main__":
    unittest.main()
