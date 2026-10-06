from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import Event
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QPointF, QRunnable, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QWidget,
)

from detect_app.vision.image import IMAGE_PROCESSING_LOCK, ImageReadError, make_preview
from detect_app.ui.theme import VIEW_BACKGROUND, VIEW_DROP_BACKGROUND, VIEW_TEXT

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
            with IMAGE_PROCESSING_LOCK:
                if self._request_cancelled.is_set() or self._viewer_destroyed.is_set():
                    return
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


class PreviewProbe(QObject):
    """Decodes a candidate preview off the UI thread; only the latest request is reported."""

    finished = Signal(object, object, object, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._generation = 0
        self._paths: dict[int, Path] = {}
        self._request_cancelled: Event | None = None
        self._destroyed = Event()
        self.destroyed.connect(self._destroyed.set)
        self._signals = ImageLoadSignals()
        self._signals.loaded.connect(self._loaded)

    @property
    def pending(self) -> bool:
        return self._request_cancelled is not None

    def request(self, path: Path) -> None:
        self.cancel()
        self._generation += 1
        self._paths = {self._generation: Path(path)}
        self._request_cancelled = Event()
        _IMAGE_LOAD_POOL.start(
            ImageLoadTask(Path(path), self._generation, self._signals, self._request_cancelled, self._destroyed)
        )

    def cancel(self) -> None:
        if self._request_cancelled is not None:
            self._request_cancelled.set()
            self._request_cancelled = None
        self._generation += 1

    def _loaded(self, generation: int, image: QImage, source_size: tuple[int, int] | None, error: str) -> None:
        if generation != self._generation or generation not in self._paths:
            return
        self._request_cancelled = None
        self.finished.emit(self._paths.pop(generation), image, source_size, error)


class ImageViewer(QGraphicsView):
    """Aspect-preserving preview viewer with boxes in oriented source coordinates.

    Overlay modes: ``all`` draws every stored box, ``selection`` only the selected box (used
    on top of the saved annotated file, which already contains every box), ``none`` nothing.
    """

    OVERLAY_ALL = "all"
    OVERLAY_SELECTION = "selection"
    OVERLAY_NONE = "none"
    CACHE_SIZE = 3

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
        self._restore_view: tuple[tuple[int, int], QTransform, QPointF] | None = None
        self._overlay_mode = self.OVERLAY_ALL
        self._cache: OrderedDict[Path, tuple[float, QImage, tuple[int, int]]] = OrderedDict()
        self._drag_evaluate: Callable[[QMimeData], bool] | None = None
        self._drag_drop: Callable[[QMimeData], None] | None = None
        self.setProperty("dropActive", False)
        self._error_text = "Image introuvable ou illisible"
        self._show_message(empty_text)

    @property
    def overlay_mode(self) -> str:
        return self._overlay_mode

    def set_overlay_mode(self, mode: str) -> None:
        self._overlay_mode = mode
        self._draw_overlays()

    def overlay_count(self) -> int:
        return len(self._overlay_items)

    def set_empty_text(self, message: str) -> None:
        self._empty_text = message
        if self._image_item is None and self._path is None:
            self._show_message(message)

    def _cache_get(self, path: Path) -> tuple[QImage, tuple[int, int]] | None:
        entry = self._cache.get(path)
        if entry is None:
            return None
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        if stamp != entry[0]:
            self._cache.pop(path, None)
            return None
        self._cache.move_to_end(path)
        return entry[1], entry[2]

    def _cache_put(self, path: Path, image: QImage, source_size: tuple[int, int]) -> None:
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return
        self._cache[path] = (stamp, image, source_size)
        self._cache.move_to_end(path)
        while len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)

    def cached_paths(self) -> list[Path]:
        return list(self._cache)

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

    def _remember_view(self) -> None:
        if self._image_item is not None and self._source_size is not None:
            center = self.mapToScene(self.viewport().rect().center())
            self._restore_view = (self._source_size, QTransform(self.transform()), center)
        else:
            self._restore_view = None

    def load_path(
        self,
        path: Path | None,
        error_text: str = "Image introuvable ou illisible",
        keep_view: bool = False,
    ) -> None:
        self._generation += 1
        self._cancel_current_request()
        generation = self._generation
        path = Path(path) if path is not None else None
        changed_path = path != self._path
        self._error_text = error_text
        if path is not None and path.is_file() and not changed_path and not self._image.isNull():
            self._draw_overlays()
            return
        if keep_view:
            self._remember_view()
        else:
            self._restore_view = None
        self._path = path
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
        self._detections = []
        self._selected_id = None
        self._fit_after_load = True
        self.setToolTip("")
        cached = self._cache_get(path)
        if cached is not None:
            self._show_ready_image(*cached)
            return
        self._image = QImage()
        self._source_size = None
        self._show_message("Chargement de l’image…")
        signals = ImageLoadSignals()
        signals.loaded.connect(self._image_loaded)
        self._request_cancelled = Event()
        _IMAGE_LOAD_POOL.start(
            ImageLoadTask(path, generation, signals, self._request_cancelled, self._viewer_destroyed)
        )

    def show_image(self, path: Path, image: QImage, source_size: tuple[int, int]) -> None:
        """Display a preview already decoded off the UI thread (e.g. by PreviewProbe)."""
        self._generation += 1
        self._cancel_current_request()
        self._path = Path(path)
        self._detections = []
        self._selected_id = None
        self._restore_view = None
        self._fit_after_load = True
        self.setToolTip("")
        self._cache_put(self._path, image, source_size)
        self._show_ready_image(image, source_size)

    def _show_ready_image(self, image: QImage, source_size: tuple[int, int]) -> None:
        self._image = image
        self._source_size = source_size
        self._draw_image()
        restore = self._restore_view
        self._restore_view = None
        if restore is not None and restore[0] == source_size:
            self._fit_after_load = False
            self.setTransform(restore[1])
            self.centerOn(restore[2])
        elif self._fit_after_load:
            self._fit_after_load = False
            self.fit_image()

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
        if self._path is not None:
            self._cache_put(self._path, image, source_size)
        self._show_ready_image(image, source_size)

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
        if self._overlay_mode == self.OVERLAY_NONE:
            return
        for detection in self._detections:
            identity = str(detection.get("id", ""))
            if self._overlay_mode == self.OVERLAY_SELECTION and identity != self._selected_id:
                continue
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

    def set_drop_handlers(
        self,
        evaluate: Callable[[QMimeData], bool] | None,
        drop: Callable[[QMimeData], None] | None,
    ) -> None:
        """Enable file drops; ``evaluate`` only drives the hover hint, ``drop`` validates."""
        self._drag_evaluate = evaluate
        self._drag_drop = drop
        self.setAcceptDrops(drop is not None)
        self.viewport().setAcceptDrops(drop is not None)

    def _set_drop_active(self, active: bool) -> None:
        if bool(self.property("dropActive")) == active:
            return
        self.setProperty("dropActive", active)
        self.setBackgroundBrush(QBrush(VIEW_DROP_BACKGROUND if active else VIEW_BACKGROUND))
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()

    def is_drop_active(self) -> bool:
        return bool(self.property("dropActive"))

    def dragEnterEvent(self, event) -> None:
        if self._drag_drop is None:
            super().dragEnterEvent(event)
            return
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        acceptable = bool(self._drag_evaluate and self._drag_evaluate(event.mimeData()))
        self._set_drop_active(acceptable)
        # Refused content is still received so the drop can explain why it is refused.
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if self._drag_drop is None:
            super().dragMoveEvent(event)
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        if self._drag_drop is None:
            super().dragLeaveEvent(event)
            return
        self._set_drop_active(False)
        event.accept()

    def dropEvent(self, event) -> None:
        if self._drag_drop is None:
            super().dropEvent(event)
            return
        self._set_drop_active(False)
        event.acceptProposedAction()
        self._drag_drop(event.mimeData())

    def wheelEvent(self, event) -> None:
        if self._image_item is None:
            return
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._message_item is not None:
            self._show_message(self._message_item.toPlainText())
