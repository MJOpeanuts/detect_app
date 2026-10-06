from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PIL import ImageDraw

from detect_app.persistence.repository import AnalysisRepository
from detect_app.services.image_source import ImageSource
from detect_app.services.models import ModelRegistry
from detect_app.vision.engine import run_inference
from detect_app.vision.types import Detection


class AnalysisService:
    SUPPORTED_IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}

    def __init__(
        self,
        repository: AnalysisRepository,
        model_registry: ModelRegistry,
        images_directory: Path,
    ):
        self._repository = repository
        self._model_registry = model_registry
        self._images_directory = images_directory.resolve()

    def resolve_image(self, relative_path: str) -> Path:
        path = (self._images_directory / relative_path).resolve()
        if not path.is_relative_to(self._images_directory):
            raise ValueError("Chemin d'image en dehors du dossier d'analyses.")
        return path

    def analyze(
        self,
        source: ImageSource,
        model_identifier: str,
        confidence_threshold: float = 0.25,
    ) -> str:
        if source.source_type not in {"manual", "arducam"}:
            raise ValueError(f"Source d'image non prise en charge : {source.source_type}")
        model = self._model_registry.get(model_identifier)
        image_path = source.image_path()
        job_id = str(uuid4())
        job_directory = self._images_directory / job_id
        job_directory.mkdir(parents=True, exist_ok=False)
        suffix = image_path.suffix.lower()
        original_path = job_directory / f"original{suffix if suffix in self.SUPPORTED_IMAGE_SUFFIXES else '.img'}"
        relative_original = original_path.relative_to(self._images_directory).as_posix()
        self._repository.create_job(
            job_id,
            relative_original,
            source.source_type,
            model.name,
            model.sha256,
        )
        try:
            shutil.copy2(image_path, original_path)
            image, detections = run_inference(
                model.path,
                original_path,
                model.class_names,
                confidence_threshold,
            )
            crops_directory = job_directory / "crops"
            if detections:
                crops_directory.mkdir()
            saved_detections: list[Detection] = []
            for index, detection in enumerate(detections, start=1):
                left = max(0, int(detection.x_min))
                top = max(0, int(detection.y_min))
                right = min(image.width, int(detection.x_max + 0.999))
                bottom = min(image.height, int(detection.y_max + 0.999))
                crop_path = None
                if right > left and bottom > top:
                    crop = crops_directory / f"{index:04d}.png"
                    image.crop((left, top, right, bottom)).save(crop)
                    crop_path = crop.relative_to(self._images_directory).as_posix()
                saved_detections.append(replace(detection, crop_path=crop_path))

            draw = ImageDraw.Draw(image)
            for detection in detections:
                label = f"{detection.class_name} {detection.confidence:.2f}"
                draw.rectangle(
                    (detection.x_min, detection.y_min, detection.x_max, detection.y_max),
                    outline=(255, 55, 55),
                    width=max(2, round(min(image.size) / 300)),
                )
                draw.text((detection.x_min, max(0, detection.y_min - 14)), label, fill=(255, 55, 55))
            annotated_path = job_directory / "annotated.png"
            image.save(annotated_path)
            self._repository.complete_job(
                job_id,
                saved_detections,
                annotated_path.relative_to(self._images_directory).as_posix(),
            )
        except MemoryError:
            self._repository.fail_job(job_id, "Mémoire insuffisante pour terminer l’analyse de cette image.")
        except Exception as exc:
            self._repository.fail_job(job_id, str(exc) or exc.__class__.__name__)
        return job_id
