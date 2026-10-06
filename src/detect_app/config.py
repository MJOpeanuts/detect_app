from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    database: Path
    logs: Path
    models: Path
    images: Path
    configuration: Path
    bundled_model: Path
    icon: Path

    @classmethod
    def create(cls) -> AppPaths:
        if os.environ.get("LOCALAPPDATA"):
            data_dir = Path(os.environ["LOCALAPPDATA"]) / "DataPeanuts" / "NutsVision"
        elif sys.platform == "win32":
            data_dir = Path.home() / "AppData" / "Local" / "DataPeanuts" / "NutsVision"
        else:
            data_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "DataPeanuts" / "NutsVision"

        default_image_root = Path.home() / "Images" / "NutsVision" / "analyses"
        image_root = Path(os.environ.get("NUTS_VISION_IMAGES", default_image_root)).expanduser().resolve()
        source_root = Path(__file__).resolve().parents[2]
        installed_root = Path(sys.prefix) / "share" / "nutsvision"
        bundled_model = source_root / "ic_detect_best.onnx"
        icon = source_root / "nuts-app.png"
        if not bundled_model.is_file() and (installed_root / "ic_detect_best.onnx").is_file():
            bundled_model = installed_root / "ic_detect_best.onnx"
            icon = installed_root / "nuts-app.png"

        paths = cls(
            data_dir=data_dir,
            database=data_dir / "database" / "nuts_vision.sqlite3",
            logs=data_dir / "logs",
            models=data_dir / "models",
            images=image_root,
            configuration=data_dir / "configuration",
            bundled_model=bundled_model,
            icon=icon,
        )
        for folder in (
            paths.database.parent,
            paths.logs,
            paths.models,
            paths.images,
            paths.configuration,
        ):
            folder.mkdir(parents=True, exist_ok=True)
        return paths
