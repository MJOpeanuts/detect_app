from __future__ import annotations

import os
import ctypes
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


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
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data and Path(local_app_data).expanduser().is_absolute():
            data_root = Path(local_app_data).expanduser()
        elif sys.platform == "win32":
            known_local_app_data = _windows_known_folder("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")
            data_root = known_local_app_data or Path.home() / "AppData" / "Local"
        else:
            xdg_data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share").expanduser()
            data_root = xdg_data_home if xdg_data_home.is_absolute() else Path.home() / ".local/share"
        data_dir = data_root / "DataPeanuts" / "detect_app"

        default_image_root = _pictures_directory() / "detect_app" / "analyses"
        configured_image_root = Path(os.environ.get("DETECT_APP_IMAGES") or default_image_root).expanduser()
        if not configured_image_root.is_absolute():
            configured_image_root = Path.home() / configured_image_root
        image_root = configured_image_root.resolve()
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
