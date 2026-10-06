from __future__ import annotations

from threading import Event
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QWidget,
)

from detect_app.vision.image import ImageReadError, make_preview
from detect_app.ui.theme import VIEW_BACKGROUND, VIEW_TEXT

_IMAGE_LOAD_POOL = QThreadPool()
_IMAGE_LOAD_POOL.setMaxThreadCount(1)


class ImageLoadSignals(QObject):
    loaded = Signal(int, object, object, str)


class ImageLoadTask(QRunnable):
    def __init__(
        self,
        path: Path,
        generation: int,
        signals: ImageLoadSignals,
        request_cancelled: Event,
        viewer_destroyed: Event,
    ):
        super().__init__()
        self._path = path
        self._generation = generation
        self._signals = signals
        self._request_cancelled = request_cancelled
        self._viewer_destroyed = viewer_destroyed

    def run(self) -> None:
        if self._request_cancelled.is_set() or self._viewer_destroyed.is_set():
            return
        try:
            preview, reference_size = make_preview(self._path)
            if self._request_cancelled.is_set() or self._viewer_destroyed.is_set():
                return
            width, height = preview.size
            image = QImage(
                preview.tobytes(),
                width,
                height,
                width * 3,
                QImage.Format.Format_RGB888,
            ).copy()
            self._signals.loaded.emit(self._generation, image, reference_size, "")
        except FileNotFoundError:
            self._signals.loaded.emit(self._generation, QImage(), None, "Fichier image introuvable.")
        except ImageReadError as exc:
            self._signals.loaded.emit(self._generation, QImage(), None, str(exc))
        except MemoryError:
            self._signals.loaded.emit(
                self._generation, QImage(), None, "Mémoire insuffisante pour décoder cette image."
            )
        except Exception as exc:
            self._signals.loaded.emit(
                self._generation,
                QImage(),
                None,
                f"Décodage impossible : {str(exc) or exc.__class__.__name__}",
            )


class ImageViewer(QGraphicsView):
    """Aspect-preserving preview viewer with boxes in oriented source coordinates."""

    def __init__(self, empty_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(VIEW_BACKGROUND)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRenderHints(self.renderHints() | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMinimumSize(300, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._empty_text = empty_text
        self._message_item: QGraphicsTextItem | None = None
        self._image_item: QGraphicsPixmapItem | None = None
        self._overlay_items: list[QGraphicsRectItem] = []
        self._image = QImage()
        self._source_size: tuple[int, int] | None = None
        self._path: Path | None = None
        self._generation = 0
        self._request_cancelled: Event | None = None
        self._viewer_destroyed = Event()
        self.destroyed.connect(self._viewer_destroyed.set)
        self._detections: list[dict] = []
        self._selected_id: str | None = None
        self._fit_after_load = True
        self._error_text = "Image introuvable ou illisible"
        self._show_message(empty_text)

    def _show_message(self, message: str) -> None:
        self._scene.clear()
        self._image_item = None
        self._overlay_items = []
        self._message_item = self._scene.addText(message)
        self._message_item.setDefaultTextColor(VIEW_TEXT)
        self._scene.setSceneRect(QRectF(0, 0, max(1, self.viewport().width()), max(1, self.viewport().height())))
        self._message_item.setPos(
            (self._scene.sceneRect().width() - self._message_item.boundingRect().width()) / 2,
            (self._scene.sceneRect().height() - self._message_item.boundingRect().height()) / 2,
        )

    def text(self) -> str:
        return self._message_item.toPlainText() if self._message_item else ""

    def pixmap(self) -> QPixmap:
        return self._image_item.pixmap() if self._image_item else QPixmap()

    def setText(self, message: str) -> None:
        self._generation += 1
        self._cancel_current_request()
        self._empty_text = message
        self._image = QImage()
        self._source_size = None
        self._show_message(message)

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def preview_is_reduced(self) -> bool:
        return (
            self._source_size is not None
            and (self._source_size[0] > self._image.width() or self._source_size[1] > self._image.height())
        )

    def _cancel_current_request(self) -> None:
        if self._request_cancelled is not None:
            self._request_cancelled.set()
            self._request_cancelled = None

    def load_path(self, path: Path | None, error_text: str = "Image introuvable ou illisible") -> None:
        self._generation += 1
        self._cancel_current_request()
        generation = self._generation
        path = Path(path) if path is not None else None
        changed_path = path != self._path
        self._path = path
        self._error_text = error_text
        if path is None:
            self._image = QImage()
            self._source_size = None
            self._detections = []
            self.setToolTip("")
            self._show_message(self._empty_text)
            return
        if not path.is_file():
            self._image = QImage()
            self._source_size = None
            self._detections = []
            self._show_message(error_text)
            return
        if not changed_path and not self._image.isNull():
            self._draw_overlays()
            return
        self._image = QImage()
        self._source_size = None
        self._detections = []
        self._selected_id = None
        self._fit_after_load = True
        self.setToolTip("")
        self._show_message("Chargement de l’image…")
        signals = ImageLoadSignals()
        signals.loaded.connect(self._image_loaded)
        self._request_cancelled = Event()
        _IMAGE_LOAD_POOL.start(
            ImageLoadTask(path, generation, signals, self._request_cancelled, self._viewer_destroyed)
        )

    def _image_loaded(
        self,
        generation: int,
        image: QImage,
        source_size: tuple[int, int] | None,
        error: str,
    ) -> None:
        if generation != self._generation:
            return
        if image.isNull() or source_size is None:
            self._image = QImage()
            self._source_size = None
            self._show_message(error or self._error_text)
            self.setToolTip(error)
            return
        self.setToolTip("")
        self._image = image
        self._source_size = source_size
        self._draw_image()
        if self._fit_after_load:
            self._fit_after_load = False
            self.fit_image()

    def _draw_image(self) -> None:
        if self._image.isNull() or self._source_size is None:
            return
        self._scene.clear()
        self._message_item = None
        self._overlay_items = []
        self._image_item = self._scene.addPixmap(QPixmap.fromImage(self._image))
        source_width, source_height = self._source_size
        self._image_item.setTransform(
            QTransform.fromScale(source_width / self._image.width(), source_height / self._image.height())
        )
        self._scene.setSceneRect(QRectF(0, 0, source_width, source_height))
        self._draw_overlays()

    def _clear_overlays(self) -> None:
        for item in self._overlay_items:
            self._scene.removeItem(item)
        self._overlay_items.clear()

    def _draw_overlays(self) -> None:
        if self._image_item is None:
            return
        self._clear_overlays()
        for detection in self._detections:
            identity = str(detection.get("id", ""))
            color = QColor("#64C7FF" if identity == self._selected_id else "#91A0AE")
            pen = QPen(color, 3 if identity == self._selected_id else 1.5)
            pen.setCosmetic(True)
            box = QRectF(
                float(detection["x_min"]),
                float(detection["y_min"]),
                float(detection["x_max"]) - float(detection["x_min"]),
                float(detection["y_max"]) - float(detection["y_min"]),
            )
            item = self._scene.addRect(box, pen)
            item.setZValue(2 if identity == self._selected_id else 1)
            self._overlay_items.append(item)

    def set_detections(self, detections: list[dict], selected_id: str | None = None) -> None:
        self._detections = list(detections)
        self._selected_id = selected_id
        self._draw_overlays()

    def select_detection(self, identity: str | None) -> None:
        self.set_detections(self._detections, identity)

    def fit_image(self) -> None:
        if self._image_item is not None:
            self.resetTransform()
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def show_actual_size(self) -> None:
        if self._image_item is not None:
            self.resetTransform()
            self.centerOn(self._image_item)

    def wheelEvent(self, event) -> None:
        if self._image_item is None:
            return
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._message_item is not None:
            self._show_message(self._message_item.toPlainText())
