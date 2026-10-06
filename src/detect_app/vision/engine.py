from __future__ import annotations

import ast
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from detect_app.vision.types import Detection
from detect_app.vision.image import open_oriented_rgb

CONFIDENCE_THRESHOLD = 0.25
IOU_THRESHOLD = 0.45


class ModelCompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class ModelInspection:
    input_name: str
    output_name: str
    input_size: int
    output_transposed: bool
    class_count: int
    class_names: tuple[str, ...] | None
    metadata: dict[str, str]


def _metadata_names(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    try:
        indexed = {int(key): str(name).strip() for key, name in parsed.items()}
    except (TypeError, ValueError):
        return None
    if sorted(indexed) != list(range(len(indexed))) or any(not name for name in indexed.values()):
        return None
    return tuple(indexed[index] for index in range(len(indexed)))


def _metadata_options(metadata: dict[str, str]) -> dict:
    value = metadata.get("args", "")
    if not value:
        return {}
    try:
        options = json.loads(value)
    except json.JSONDecodeError:
        try:
            options = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return {}
    return options if isinstance(options, dict) else {}


def _metadata_flag(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def inspect_model(model_path: str | Path) -> ModelInspection:
    model_path = Path(model_path)
    if model_path.suffix.lower() != ".onnx" or not model_path.is_file():
        raise ModelCompatibilityError("Sélectionnez un fichier ONNX existant.")
    if "CPUExecutionProvider" not in ort.get_available_providers():
        raise ModelCompatibilityError("ONNX Runtime ne fournit pas CPUExecutionProvider.")
    try:
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    except Exception as exc:
        raise ModelCompatibilityError(f"Le fichier ONNX ne peut pas être chargé : {exc}") from exc

    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1:
        raise ModelCompatibilityError("Format non pris en charge : un seul tenseur d'entrée et de sortie est requis.")
    input_tensor, output_tensor = inputs[0], outputs[0]
    if input_tensor.type != "tensor(float)" or len(input_tensor.shape) != 4:
        raise ModelCompatibilityError("L'entrée doit être un tenseur float de rang 4 (batch, canaux, hauteur, largeur).")
    if input_tensor.shape[0] not in (1, "1") or input_tensor.shape[1] not in (3, "3"):
        raise ModelCompatibilityError("Seuls les modèles à batch 1 et trois canaux RGB sont pris en charge.")
    height, width = input_tensor.shape[2], input_tensor.shape[3]
    if not isinstance(height, int) or not isinstance(width, int) or height != width:
        raise ModelCompatibilityError("La taille d'entrée doit être carrée et fixe (par exemple 640 × 640).")
    if len(output_tensor.shape) != 3 or output_tensor.type != "tensor(float)":
        raise ModelCompatibilityError("La sortie doit être un tenseur float de détections brutes de rang 3.")

    model_metadata = session.get_modelmeta()
    metadata = dict(model_metadata.custom_metadata_map or {})
    producer = model_metadata.producer_name or ""
    if "ultralytics" not in f"{metadata.get('author', '')} {producer}".lower():
        raise ModelCompatibilityError(
            "Le format pris en charge est un export de détection Ultralytics YOLO avec métadonnées vérifiables."
        )
    if metadata.get("task", "").lower() != "detect":
        raise ModelCompatibilityError("Le modèle n'est pas un détecteur d'objets.")
    options = _metadata_options(metadata)
    if (
        _metadata_flag(metadata.get("nms"))
        or _metadata_flag(metadata.get("end2end"))
        or _metadata_flag(options.get("nms"))
        or _metadata_flag(options.get("end2end"))
    ):
        raise ModelCompatibilityError("Les sorties avec NMS intégrée ne sont pas prises en charge par cet adaptateur.")

    class_names = _metadata_names(metadata.get("names"))
    first, second = output_tensor.shape[1], output_tensor.shape[2]
    known_count = len(class_names) if class_names else None
    candidates = []
    for transposed, feature_dimension, anchor_dimension in (
        (True, first, second),
        (False, second, first),
    ):
        if (
            isinstance(feature_dimension, int)
            and isinstance(anchor_dimension, int)
            and 4 < feature_dimension < anchor_dimension
        ):
            count = feature_dimension - 4
            if known_count is None or count == known_count:
                candidates.append((transposed, count))
    if len(candidates) != 1:
        raise ModelCompatibilityError(
            "La sortie ne correspond pas au format Ultralytics brut [batch, 4 + classes, ancres] "
            "ou [batch, ancres, 4 + classes]."
        )
    transposed, class_count = candidates[0]
    if known_count is not None and known_count != class_count:
        raise ModelCompatibilityError("Les noms de classes des métadonnées ne correspondent pas à la sortie.")
    if metadata.get("channels", "3") != "3":
        raise ModelCompatibilityError("Les métadonnées du modèle indiquent un nombre de canaux incompatible.")
    return ModelInspection(
        input_name=input_tensor.name,
        output_name=output_tensor.name,
        input_size=height,
        output_transposed=transposed,
        class_count=class_count,
        class_names=class_names,
        metadata=metadata,
    )


def _letterbox(image: Image.Image, size: int) -> tuple[np.ndarray, float, int, int]:
    width, height = image.size
    scale = min(size / width, size / height)
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    resized = image.resize((resized_width, resized_height), Image.Resampling.BILINEAR)
    left = (size - resized_width) // 2
    top = (size - resized_height) // 2
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(resized, (left, top))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None, ...] / 255.0
    return np.ascontiguousarray(tensor), scale, left, top


def _iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    left = np.maximum(box[0], boxes[:, 0])
    top = np.maximum(box[1], boxes[:, 1])
    right = np.minimum(box[2], boxes[:, 2])
    bottom = np.minimum(box[3], boxes[:, 3])
    intersection = np.maximum(0.0, right - left) * np.maximum(0.0, bottom - top)
    box_area = (box[2] - box[0]) * (box[3] - box[1])
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return intersection / np.maximum(box_area + areas - intersection, 1e-9)


def _nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray) -> list[int]:
    selected: list[int] = []
    for class_id in np.unique(classes):
        indices = np.flatnonzero(classes == class_id)
        indices = indices[np.argsort(scores[indices])[::-1]]
        class_selected = 0
        while indices.size and class_selected < 300:
            current = int(indices[0])
            selected.append(current)
            class_selected += 1
            indices = indices[1:]
            if indices.size:
                indices = indices[_iou(boxes[current], boxes[indices]) <= IOU_THRESHOLD]
    selected.sort(key=lambda index: float(scores[index]), reverse=True)
    return selected[:300]


def run_inference(
    model_path: str | Path,
    image_path: str | Path,
    class_names: tuple[str, ...],
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[Image.Image, list[Detection]]:
    inspection = inspect_model(model_path)
    if len(class_names) != inspection.class_count:
        raise ModelCompatibilityError("Le mapping des classes ne correspond pas au modèle.")

    original = open_oriented_rgb(image_path)
    tensor, scale, pad_x, pad_y = _letterbox(original, inspection.input_size)
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    raw = session.run([inspection.output_name], {inspection.input_name: tensor})[0]
    predictions = np.asarray(raw, dtype=np.float32)[0]
    if inspection.output_transposed:
        predictions = predictions.T
    expected_features = 4 + inspection.class_count
    if predictions.ndim != 2 or predictions.shape[1] != expected_features:
        raise ModelCompatibilityError("La sortie à l'exécution ne respecte pas la signature inspectée.")
    finite = np.isfinite(predictions).all(axis=1)
    predictions = predictions[finite]
    if not len(predictions):
        return original, []

    scores = predictions[:, 4:]
    class_ids = np.argmax(scores, axis=1).astype(np.int32)
    confidences = scores[np.arange(len(scores)), class_ids]
    valid = np.isfinite(confidences) & (confidences >= confidence_threshold)
    predictions, confidences, class_ids = predictions[valid], confidences[valid], class_ids[valid]
    if not len(predictions):
        return original, []

    centers = predictions[:, :2].copy()
    sizes = predictions[:, 2:4].copy()
    boxes = np.column_stack(
        (
            centers[:, 0] - sizes[:, 0] / 2,
            centers[:, 1] - sizes[:, 1] / 2,
            centers[:, 0] + sizes[:, 0] / 2,
            centers[:, 1] + sizes[:, 1] / 2,
        )
    )
    boxes[:, (0, 2)] = (boxes[:, (0, 2)] - pad_x) / scale
    boxes[:, (1, 3)] = (boxes[:, (1, 3)] - pad_y) / scale
    boxes[:, (0, 2)] = np.clip(boxes[:, (0, 2)], 0, original.width)
    boxes[:, (1, 3)] = np.clip(boxes[:, (1, 3)], 0, original.height)
    valid_boxes = np.isfinite(boxes).all(axis=1)
    valid_boxes &= (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    boxes, confidences, class_ids = boxes[valid_boxes], confidences[valid_boxes], class_ids[valid_boxes]
    selected = _nms(boxes, confidences, class_ids)

    detections = [
        Detection(
            class_id=int(class_ids[index]),
            class_name=class_names[int(class_ids[index])],
            confidence=float(np.clip(confidences[index], 0, 1)),
            x_min=float(boxes[index, 0]),
            y_min=float(boxes[index, 1]),
            x_max=float(boxes[index, 2]),
            y_max=float(boxes[index, 3]),
        )
        for index in selected
        if math.isfinite(float(confidences[index]))
    ]
    return original, detections
