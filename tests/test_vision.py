import unittest
import ast
from pathlib import Path

import numpy as np
from PIL import Image

from detect_app.vision.engine import (
    ModelCompatibilityError,
    _letterbox,
    _metadata_names,
    _nms,
    inspect_model,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class VisionTests(unittest.TestCase):
    def test_supplied_model_signature_and_metadata_are_explicit(self):
        model = inspect_model(REPOSITORY_ROOT / "ic_detect_best.onnx")
        self.assertEqual(model.input_name, "images")
        self.assertEqual(model.output_name, "output0")
        self.assertEqual(model.input_size, 640)
        self.assertEqual(model.class_names, ("four_side", "two_side", "without_side"))
        self.assertEqual(model.class_count, 3)
        self.assertTrue(model.output_transposed)
        self.assertFalse(ast.literal_eval(model.metadata["args"])["nms"])

    def test_metadata_mapping_does_not_infer_an_order_from_a_list(self):
        self.assertEqual(_metadata_names("{0: 'A', 1: 'B'}"), ("A", "B"))
        self.assertIsNone(_metadata_names("{1: 'B', 2: 'C'}"))
        self.assertIsNone(_metadata_names("['A', 'B']"))

    def test_letterbox_preserves_aspect_ratio_and_rgb_tensor(self):
        image = Image.new("RGB", (200, 100), (255, 0, 0))
        tensor, scale, left, top = _letterbox(image, 640)
        self.assertEqual(tensor.shape, (1, 3, 640, 640))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertAlmostEqual(scale, 3.2)
        self.assertEqual((left, top), (0, 160))
        self.assertTrue(np.allclose(tensor[0, :, 200, 10], (1.0, 0.0, 0.0)))

    def test_nms_suppresses_overlapping_boxes_only_within_each_class(self):
        boxes = np.array([[0, 0, 10, 10], [1, 1, 9, 9], [1, 1, 9, 9]], dtype=np.float32)
        scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
        classes = np.array([0, 0, 1], dtype=np.int32)
        self.assertEqual(set(_nms(boxes, scores, classes)), {0, 2})

    def test_unsupported_model_path_has_clear_error(self):
        with self.assertRaises(ModelCompatibilityError):
            inspect_model(REPOSITORY_ROOT / "README.md")


if __name__ == "__main__":
    unittest.main()
