from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from detect_app.vision.engine import ModelCompatibilityError, ModelInspection, inspect_model


@dataclass(frozen=True)
class ModelSpec:
    identifier: str
    name: str
    path: Path
    sha256: str
    class_names: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ModelRegistry:
    def __init__(self, model_directory: Path, configuration_directory: Path):
        self.model_directory = model_directory
        self.configuration_directory = configuration_directory / "models"
        self.model_directory.mkdir(parents=True, exist_ok=True)
        self.configuration_directory.mkdir(parents=True, exist_ok=True)

    def inspect_candidate(self, path: Path) -> ModelInspection:
        return inspect_model(path)

    def register(self, source: Path, supplied_names: tuple[str, ...] | None = None) -> ModelSpec:
        inspection = self.inspect_candidate(source)
        names = inspection.class_names or supplied_names
        if names is None:
            raise ModelCompatibilityError(
                f"Le modèle contient {inspection.class_count} classes, mais aucun mapping n'est fourni."
            )
        names = tuple(name.strip() for name in names)
        if len(names) != inspection.class_count or any(not name for name in names):
            raise ModelCompatibilityError(
                f"Fournissez exactement {inspection.class_count} noms de classes, dans l'ordre des class_id."
            )
        if inspection.class_names is not None and names != inspection.class_names:
            raise ModelCompatibilityError(
                "Le mapping fourni diffère des noms intégrés aux métadonnées du modèle."
            )

        digest = sha256_file(source)
        safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", source.stem).strip("_") or "modele"
        model_file = f"{safe_stem}-{digest[:8]}.onnx"
        target = self.model_directory / model_file
        configuration_path = self.configuration_directory / f"{safe_stem}-{digest[:8]}.json"
        if not target.exists():
            shutil.copy2(source, target)
        config = {
            "name": safe_stem,
            "model_file": model_file,
            "sha256": digest,
            "class_names": {str(index): name for index, name in enumerate(names)},
            "adapter": "ultralytics-yolo-raw",
        }
        temporary = configuration_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(configuration_path)
        return ModelSpec(configuration_path.stem, safe_stem, target, digest, names)

    def ensure_bundled_model(self, bundled_model: Path) -> ModelSpec | None:
        if not bundled_model.is_file():
            return None
        return self.register(bundled_model)

    def list_models(self) -> list[ModelSpec]:
        models: list[ModelSpec] = []
        for configuration_path in sorted(self.configuration_directory.glob("*.json")):
            try:
                config = json.loads(configuration_path.read_text(encoding="utf-8"))
                model_path = (self.model_directory / config["model_file"]).resolve()
                if model_path.parent != self.model_directory.resolve() or not model_path.is_file():
                    continue
                digest = sha256_file(model_path)
                if digest != config["sha256"]:
                    continue
                names_mapping = config["class_names"]
                names = tuple(names_mapping[str(index)] for index in range(len(names_mapping)))
                inspection = inspect_model(model_path)
                if len(names) != inspection.class_count:
                    continue
                if inspection.class_names is not None and names != inspection.class_names:
                    continue
                if config.get("adapter") != "ultralytics-yolo-raw":
                    continue
                models.append(
                    ModelSpec(
                        configuration_path.stem,
                        config["name"],
                        model_path,
                        digest,
                        names,
                    )
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return models

    def get(self, identifier: str) -> ModelSpec:
        for model in self.list_models():
            if model.identifier == identifier:
                return model
        raise ModelCompatibilityError("Le modèle sélectionné est introuvable ou son empreinte a changé.")
