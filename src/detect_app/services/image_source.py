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


def check_local_image_file(path: Path) -> str | None:
    """First import check shared by the file dialog and drag-and-drop.

    Content is validated afterwards by decoding a preview off the UI thread; the
    extension alone is never trusted.
    """
    try:
        if path.is_dir():
            return "Les dossiers ne sont pas acceptés : importez un fichier image."
        if not path.is_file():
            return "Fichier introuvable."
        with path.open("rb") as file:
            if not file.read(1):
                return "Le fichier est vide."
    except PermissionError:
        return "Le fichier ne peut pas être lu (accès refusé)."
    except OSError as exc:
        return f"Le fichier ne peut pas être lu : {exc.strerror or exc}"
    return None
