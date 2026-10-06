from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ImageSource(Protocol):
    """Image provider seam; hardware integrations only need to return an image path."""

    source_type: str

    def image_path(self) -> Path: ...


class ManualImageSource:
    source_type = "manual"

    def __init__(self, path: Path):
        self._path = path

    def image_path(self) -> Path:
        return self._path
