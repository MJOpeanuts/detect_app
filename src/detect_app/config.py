from __future__ import annotations

import os
import sys
import ctypes
import uuid
from dataclasses import dataclass
from pathlib import Path


MAX_IMAGE_PIXELS = 120_000_000
MAX_PREVIEW_DIMENSION = 2048
MAX_PREVIEW_PIXELS = MAX_PREVIEW_DIMENSION * MAX_PREVIEW_DIMENSION


def _windows_known_folder(folder_id: str) -> Path | None:
    if sys.platform != "win32":
        return None

    class GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        )

    parsed = uuid.UUID(folder_id)
    guid = GUID(
        parsed.time_low,
        parsed.time_mid,
        parsed.time_hi_version,
        (ctypes.c_ubyte * 8).from_buffer_copy(parsed.bytes[8:]),
    )
    result = ctypes.c_wchar_p()
    status = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(result)
    )
    if status != 0:
        return None
    try:
        return Path(result.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(result)


def _pictures_directory() -> Path:
    windows_pictures = _windows_known_folder("33E28130-4E1E-4676-835A-98395C3BC3BB")
    if windows_pictures is not None:
        return windows_pictures
    return Path.home() / "Pictures"


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
            data_dir = Path(os.environ["LOCALAPPDATA"]) / "DataPeanuts" / "detect_app"
        elif sys.platform == "win32":
            local_app_data = _windows_known_folder("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")
            data_dir = (local_app_data or Path.home() / "AppData" / "Local") / "DataPeanuts" / "detect_app"
        else:
            data_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "DataPeanuts" / "detect_app"

        default_image_root = _pictures_directory() / "detect_app" / "analyses"
        image_root = Path(os.environ.get("DETECT_APP_IMAGES", default_image_root)).expanduser().resolve()
        source_root = Path(__file__).resolve().parents[2]
        resource_roots = (source_root, Path(sys.prefix) / "share" / "detect_app")
        resource_root = next(
            (root for root in resource_roots if (root / "ic_detect_best.onnx").is_file()),
            resource_roots[0],
        )
        bundled_model = resource_root / "ic_detect_best.onnx"
        icon = resource_root / "nuts-app.png"

        paths = cls(
            data_dir=data_dir,
            database=data_dir / "database" / "detect_app.sqlite3",
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
