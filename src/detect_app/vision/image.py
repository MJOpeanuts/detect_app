from __future__ import annotations

import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_PIXELS = 120_000_000
MAX_IMAGE_DIMENSION = 32_768
MAX_ESTIMATED_IMAGE_MEMORY = 2 * 1024 * 1024 * 1024
MAX_PREVIEW_DIMENSION = 2048
SUPPORTED_IMAGE_FORMATS = {"BMP", "JPEG", "PNG", "TIFF", "WEBP"}
SUPPORTED_IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


class ImageReadError(ValueError):
    pass


class UnsupportedImageError(ImageReadError):
    pass


class CorruptImageError(ImageReadError):
    pass


class ImageTooLargeError(ImageReadError):
    pass


class ImageMemoryLimitError(ImageReadError):
    pass


def _open_checked(path: str | Path) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            opened = Image.open(path)
            if opened.format not in SUPPORTED_IMAGE_FORMATS:
                image_format = opened.format or "inconnu"
                opened.close()
                raise UnsupportedImageError(f"Format d’image non pris en charge : {image_format}.")
            width, height = opened.size
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
                opened.close()
                raise ImageTooLargeError(
                    f"Image trop grande : {width} × {height} pixels, limite {MAX_IMAGE_PIXELS:,} pixels."
                )
            mode_bytes = {
                "1": 1,
                "L": 1,
                "P": 1,
                "I;16": 2,
                "I": 4,
                "F": 4,
                "RGB": 3,
                "RGBA": 4,
                "CMYK": 4,
            }.get(opened.mode, 4)
            estimated_memory = width * height * (mode_bytes * 3 + 3) + 128 * 1024 * 1024
            if estimated_memory > MAX_ESTIMATED_IMAGE_MEMORY:
                opened.close()
                raise ImageMemoryLimitError(
                    "Décodage refusé : mémoire estimée "
                    f"{estimated_memory / (1024 * 1024):.0f} Mio, plafond "
                    f"{MAX_ESTIMATED_IMAGE_MEMORY / (1024 * 1024):.0f} Mio."
                )
            return opened
    except FileNotFoundError:
        raise
    except ImageReadError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageTooLargeError(f"Image trop grande : Pillow a refusé son décodage ({exc}).") from exc
    except UnidentifiedImageError as exc:
        if Path(path).suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
            raise CorruptImageError("Fichier image corrompu ou illisible : format non reconnu.") from exc
        raise UnsupportedImageError("Format d’image non pris en charge.") from exc
    except OSError as exc:
        raise CorruptImageError(f"Fichier image corrompu ou illisible : {exc}") from exc


def open_oriented_rgb(path: str | Path) -> Image.Image:
    opened = _open_checked(path)
    try:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.load()
        return image
    except (ImageReadError, FileNotFoundError):
        raise
    except OSError as exc:
        raise CorruptImageError(f"Fichier image corrompu ou illisible : {exc}") from exc
    finally:
        opened.close()


def make_preview(path: str | Path) -> tuple[Image.Image, tuple[int, int]]:
    opened = _open_checked(path)
    try:
        oriented = ImageOps.exif_transpose(opened)
        reference_size = oriented.size
        oriented.thumbnail(
            (MAX_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        preview = oriented.convert("RGB")
        preview.load()
        return preview, reference_size
    except (ImageReadError, FileNotFoundError):
        raise
    except OSError as exc:
        raise CorruptImageError(f"Fichier image corrompu ou illisible : {exc}") from exc
    finally:
        opened.close()
