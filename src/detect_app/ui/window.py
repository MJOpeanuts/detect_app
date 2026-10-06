from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QRectF, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from detect_app.config import AppPaths
from detect_app.persistence.repository import AnalysisRepository
from detect_app.services.analysis import AnalysisService
from detect_app.services.image_source import ManualImageSource
from detect_app.services.models import ModelRegistry
from detect_app.vision.engine import ModelCompatibilityError


APP_STYLESHEET = """
* { font-family: "Segoe UI"; font-size: 10pt; }
QMainWindow, QWidget { background: #181A1D; color: #F2F3F5; }
QLabel { color: #F2F3F5; }
QLabel[secondary="true"] { color: #A9B0BA; }
QLabel#activeStatus { color: #A9B0BA; padding: 4px 8px; }
QLabel#activeStatus[kind="success"] { color: #83D6A3; }
QLabel#activeStatus[kind="error"] { color: #F18B86; }
QLabel#activeStatus[kind="warning"] { color: #E9C46A; }
QTabWidget::pane { border: 0; background: #181A1D; }
QTabBar::tab {
    color: #A9B0BA; background: transparent; border: 0;
    padding: 9px 14px; margin: 0 2px; border-radius: 6px;
}
QTabBar::tab:selected { color: #F2F3F5; background: #2B2F35; font-weight: 600; }
QTabBar::tab:hover:!selected { background: #22252A; color: #F2F3F5; }
QTabBar::tab:focus { outline: 1px solid #A9B0BA; }
QPushButton {
    color: #202328; background: #DDE1E6; border: 1px solid #DDE1E6;
    border-radius: 7px; padding: 7px 11px; min-height: 20px;
}
QPushButton:hover { background: #F2F3F5; border-color: #F2F3F5; }
QPushButton:pressed { background: #BFC5CD; border-color: #BFC5CD; }
QPushButton:disabled { color: #777E87; background: #343940; border-color: #343940; }
QPushButton:focus { border: 2px solid #A9B0BA; padding: 6px 10px; }
QPushButton#analyzeButton {
    color: #202328; background: #F28C28; border-color: #F28C28;
    font-weight: 700; padding-left: 15px; padding-right: 15px;
}
QPushButton#analyzeButton:hover { background: #FFA243; border-color: #FFA243; }
QPushButton#analyzeButton:pressed { background: #D87317; border-color: #D87317; }
QPushButton#analyzeButton:disabled { color: #817368; background: #59432F; border-color: #59432F; }
QComboBox, QDoubleSpinBox, QLineEdit {
    background: #2B2F35; color: #F2F3F5; border: 1px solid #3A4048;
    border-radius: 6px; padding: 6px 8px; min-height: 20px;
}
QComboBox:hover, QDoubleSpinBox:hover { border-color: #737B86; }
QComboBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border: 1px solid #A9B0BA; }
QComboBox:disabled, QDoubleSpinBox:disabled { color: #777E87; background: #22252A; }
QComboBox QAbstractItemView {
    background: #2B2F35; color: #F2F3F5; selection-background-color: #3A4048;
    border: 1px solid #3A4048;
}
QListWidget, QTableWidget {
    background: #22252A; alternate-background-color: #272A30;
    color: #F2F3F5; border: 1px solid #3A4048; border-radius: 7px;
    gridline-color: #3A4048; selection-background-color: #39414B;
    selection-color: #F2F3F5; outline: 0;
}
QListWidget::item { padding: 9px 8px; border-bottom: 1px solid #3A4048; }
QListWidget::item:hover { background: #2B2F35; }
QListWidget::item:selected { background: #343A42; border-left: 3px solid #DDE1E6; }
QHeaderView::section {
    background: #2B2F35; color: #A9B0BA; border: 0;
    border-bottom: 1px solid #3A4048; padding: 7px;
}
QTableWidget::item { padding: 5px; }
QSplitter::handle { background: #3A4048; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QGroupBox {
    color: #A9B0BA; border: 1px solid #3A4048; border-radius: 7px;
    margin-top: 10px; padding: 10px 8px 8px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QProgressBar { background: #2B2F35; border: 0; border-radius: 2px; max-height: 3px; }
QProgressBar::chunk { background: #F28C28; border-radius: 2px; }
QScrollBar:vertical { background: #181A1D; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #3A4048; border-radius: 4px; min-height: 20px; }
QScrollBar:horizontal { background: #181A1D; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #3A4048; border-radius: 4px; min-width: 20px; }
QToolTip { color: #F2F3F5; background: #22252A; border: 1px solid #3A4048; }
QMessageBox, QFileDialog, QInputDialog { background: #181A1D; }
"""

VIEW_BACKGROUND = QColor("#22252A")
VIEW_TEXT = QColor("#A9B0BA")
MAX_SYNC_IMAGE_BYTES = 8 * 1024 * 1024


class ImageLoadSignals(QObject):
    loaded = Signal(int, object, str)


class ImageLoadTask(QRunnable):
    def __init__(self, path: Path, generation: int, signals: ImageLoadSignals):
        super().__init__()
        self._path = path
        self._generation = generation
        self._signals = signals

    def run(self) -> None:
        reader = QImageReader(str(self._path))
        reader.setAutoTransform(True)
        image = reader.read()
        self._signals.loaded.emit(
            self._generation,
            image,
            "" if not image.isNull() else reader.errorString(),
        )


class ImageViewer(QGraphicsView):
    """Aspect-preserving, pointer-zoomable image viewer with interactive boxes."""

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
        self._image = QImage()
        self._path: Path | None = None
        self._generation = 0
        self._signals = ImageLoadSignals()
        self._signals.loaded.connect(self._image_loaded)
        self._detections: list[dict] = []
        self._selected_id: str | None = None
        self._fit_after_load = True
        self._show_message(empty_text)

    def _show_message(self, message: str) -> None:
        self._scene.clear()
        self._image_item = None
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
        self._empty_text = message
        self._show_message(message)

    @property
    def path(self) -> Path | None:
        return self._path

    def load_path(self, path: Path | None, error_text: str = "Image introuvable ou illisible") -> None:
        self._generation += 1
        generation = self._generation
        path = Path(path) if path is not None else None
        changed_path = path != self._path
        self._path = path
        if path is None:
            self._image = QImage()
            self._show_message(self._empty_text)
            return
        if not path.is_file():
            self._image = QImage()
            self._show_message(error_text)
            return
        if not changed_path and not self._image.isNull():
            self._draw_image()
            return
        self._fit_after_load = True
        try:
            is_small = path.stat().st_size <= MAX_SYNC_IMAGE_BYTES
        except OSError:
            is_small = False
        if is_small:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            self._image_loaded(generation, image, "" if not image.isNull() else reader.errorString())
            return
        self._show_message("Chargement de l’image…")
        QThreadPool.globalInstance().start(ImageLoadTask(path, generation, self._signals))

    def _image_loaded(self, generation: int, image: QImage, error: str) -> None:
        if generation != self._generation:
            return
        if image.isNull():
            self._image = QImage()
            self._show_message("Image introuvable ou illisible")
            self.setToolTip(error)
            return
        self.setToolTip("")
        self._image = image
        self._draw_image()
        if self._fit_after_load:
            self._fit_after_load = False
            self.fit_image()

    def _draw_image(self) -> None:
        if self._image.isNull():
            return
        self._scene.clear()
        self._message_item = None
        self._image_item = self._scene.addPixmap(QPixmap.fromImage(self._image))
        self._scene.setSceneRect(QRectF(0, 0, self._image.width(), self._image.height()))
        for detection in self._detections:
            self._draw_detection(detection)

    def _draw_detection(self, detection: dict) -> None:
        if self._image_item is None:
            return
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

    def set_detections(self, detections: list[dict], selected_id: str | None = None) -> None:
        self._detections = list(detections)
        self._selected_id = selected_id
        if not self._image.isNull():
            transform = self.transform()
            center = self.mapToScene(self.viewport().rect().center())
            self._draw_image()
            self.setTransform(transform)
            self.centerOn(center)

    def select_detection(self, identity: str | None) -> None:
        self.set_detections(self._detections, identity)

    def fit_image(self) -> None:
        if self._image_item is not None:
            self.resetTransform()
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)

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


class ObjectPanel(QWidget):
    selected = Signal(object)

    def __init__(self, service: AnalysisService, parent: QWidget | None = None):
        super().__init__(parent)
        self._service = service
        self._objects: dict[str, dict] = {}
        self._selected_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        heading = QLabel("Objets")
        heading.setStyleSheet("font-size: 11pt; font-weight: 600;")
        layout.addWidget(heading)
        self.summary = QLabel("Aucun résultat")
        self.summary.setProperty("secondary", True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Classe", "Confiance"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 2)
        self.object_details = QLabel("Sélectionnez un objet pour afficher sa découpe.")
        self.object_details.setProperty("secondary", True)
        self.object_details.setWordWrap(True)
        layout.addWidget(self.object_details)
        self.crop = ImageViewer("Aucune découpe disponible")
        self.crop.setMinimumHeight(130)
        self.crop.setMaximumHeight(210)
        layout.addWidget(self.crop, 1)
        self.diagnostic = QGroupBox("Diagnostic")
        self.diagnostic.setCheckable(True)
        self.diagnostic.setChecked(False)
        diagnostic_layout = QVBoxLayout(self.diagnostic)
        self.diagnostic_text = QLabel("Aucun détail technique.")
        self.diagnostic_text.setProperty("secondary", True)
        self.diagnostic_text.setWordWrap(True)
        diagnostic_layout.addWidget(self.diagnostic_text)
        layout.addWidget(self.diagnostic)
        self.diagnostic.toggled.connect(self.diagnostic_text.setVisible)
        self.diagnostic_text.hide()

    def set_result(self, objects: list[dict], summary: str, diagnostic: str = "") -> None:
        self._objects = {str(obj.get("id", index)): obj for index, obj in enumerate(objects)}
        self._selected_id = None
        self.summary.setText(summary)
        self.diagnostic_text.setText(diagnostic or "Aucun détail technique.")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row, (identity, obj) in enumerate(self._objects.items()):
            self.table.insertRow(row)
            name_item = QTableWidgetItem(str(obj.get("class_name", "Objet")))
            name_item.setData(Qt.ItemDataRole.UserRole, identity)
            confidence_item = QTableWidgetItem(f"{float(obj.get('confidence', 0)):.1%}")
            confidence_item.setData(Qt.ItemDataRole.UserRole, identity)
            confidence_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, confidence_item)
        self.table.setSortingEnabled(True)
        if self.table.rowCount():
            self.table.selectRow(0)
        else:
            self._show_object(None)
            self.selected.emit(None)

    def _selection_changed(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self._show_object(None)
            self.selected.emit(None)
            return
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        identity = str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""
        obj = self._objects.get(identity)
        self._selected_id = identity if obj is not None else None
        self._show_object(obj)
        self.selected.emit(obj)

    def _show_object(self, obj: dict | None) -> None:
        if obj is None:
            self.object_details.setText("Sélectionnez un objet pour afficher sa découpe.")
            self.crop.load_path(None)
            return
        coordinates = (
            f"x1={obj['x_min']:.1f}, y1={obj['y_min']:.1f}, "
            f"x2={obj['x_max']:.1f}, y2={obj['y_max']:.1f}"
        )
        self.object_details.setText(f"{obj['class_name']} · {obj['confidence']:.1%}")
        details = coordinates
        try:
            crop_path = self._service.resolve_image(obj["crop_path"]) if obj.get("crop_path") else None
        except (OSError, ValueError, TypeError) as exc:
            crop_path = None
            details += f"\nDécoupe indisponible : {exc}"
        if crop_path is None or not crop_path.is_file():
            self.crop.load_path(None)
            self.crop.setText("Découpe introuvable")
        else:
            self.crop.load_path(crop_path)
        self.diagnostic_text.setText(details)

    def selected_id(self) -> str | None:
        return self._selected_id


class AnalysisWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        service: AnalysisService,
        source: ManualImageSource,
        model_id: str,
        confidence_threshold: float,
    ):
        super().__init__()
        self._service = service
        self._source = source
        self._model_id = model_id
        self._confidence_threshold = confidence_threshold

    def run(self) -> None:
        try:
            self.finished.emit(
                self._service.analyze(
                    self._source,
                    self._model_id,
                    self._confidence_threshold,
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)


class MainWindow(QMainWindow):
    def __init__(
        self,
        paths: AppPaths,
        repository: AnalysisRepository,
        model_registry: ModelRegistry,
        service: AnalysisService,
        startup_error: str | None = None,
    ):
        super().__init__()
        self._paths = paths
        self._repository = repository
        self._model_registry = model_registry
        self._service = service
        self._selected_image: Path | None = None
        self._preview_path: Path | None = None
        self._input_preview_path: Path | None = None
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self._close_when_finished = False
        self._fullscreen_geometry = None
        self._fullscreen_maximized = False
        self._active_job_id: str | None = None
        self.setWindowTitle("Nuts Vision")
        self.setMinimumSize(1024, 640)
        icon_file = paths.icon.parent / "nuts-app.ico"
        if not icon_file.is_file():
            icon_file = paths.icon
        if icon_file.is_file():
            self.setWindowIcon(QIcon(str(icon_file)))
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()
        self._refresh_models()
        self._refresh_history()
        if startup_error:
            self._set_active_status("Modèle initial indisponible", "warning")
            self._results.set_result([], "Aucun résultat", startup_error)
        elif self._models.count() == 0:
            self._set_active_status("Aucun modèle compatible", "warning")
            self._results.set_result([], "Ajoutez un modèle compatible.")
        else:
            self._set_active_status("Aucune image sélectionnée")
        self._update_analyze_enabled()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 12, 16, 10)
        root_layout.setSpacing(8)
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setCornerWidget(self._brand_corner(), Qt.Corner.TopLeftCorner)
        self._active_status = QLabel("Prêt")
        self._active_status.setObjectName("activeStatus")
        self._active_status.setProperty("kind", "normal")
        self._active_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedWidth(90)
        self._progress.hide()
        status_corner = QWidget()
        status_layout = QHBoxLayout(status_corner)
        status_layout.setContentsMargins(8, 0, 0, 0)
        status_layout.setSpacing(6)
        status_layout.addWidget(self._progress)
        status_layout.addWidget(self._active_status)
        self._tabs.setCornerWidget(status_corner, Qt.Corner.TopRightCorner)
        self._tabs.currentChanged.connect(self._page_changed)

        analysis_page = QWidget()
        analysis_layout = QVBoxLayout(analysis_page)
        analysis_layout.setContentsMargins(0, 10, 0, 0)
        analysis_layout.setSpacing(10)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._choose_image = QPushButton("Importer une image")
        self._choose_image.clicked.connect(self._select_image)
        self._models = QComboBox()
        self._models.setMinimumWidth(150)
        self._models.setMaximumWidth(250)
        self._models.currentIndexChanged.connect(self._update_analyze_enabled)
        self._add_model = QPushButton("Ajouter un modèle")
        self._add_model.setToolTip("Ajouter un modèle ONNX compatible")
        self._add_model.clicked.connect(self._add_model_from_file)
        self._confidence = QDoubleSpinBox()
        self._confidence.setRange(0, 100)
        self._confidence.setDecimals(1)
        self._confidence.setSingleStep(0.5)
        self._confidence.setValue(25)
        self._confidence.setSuffix(" %")
        self._confidence.setKeyboardTracking(False)
        self._confidence.setFixedWidth(105)
        self._analyze = QPushButton("Analyser")
        self._analyze.setObjectName("analyzeButton")
        squirrel_icon = self._paths.icon.parent / "squirrel.svg"
        if squirrel_icon.is_file():
            self._analyze.setIcon(QIcon(str(squirrel_icon)))
        self._analyze.setDefault(False)
        self._analyze.clicked.connect(self._start_analysis)
        controls.addWidget(self._choose_image)
        controls.addWidget(QLabel("Modèle"))
        controls.addWidget(self._models, 1)
        controls.addWidget(self._add_model)
        controls.addSpacing(4)
        controls.addWidget(QLabel("Confiance minimale"))
        controls.addWidget(self._confidence)
        controls.addWidget(self._analyze)
        analysis_layout.addLayout(controls)

        self._input_name = QLabel("Aucune image d’entrée")
        self._input_name.setProperty("secondary", True)
        analysis_layout.addWidget(self._input_name)
        analysis_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._input_image = ImageViewer("Importez une image pour commencer.")
        self._results = ObjectPanel(self._service)
        self._results.selected.connect(self._analysis_object_selected)
        analysis_splitter.addWidget(self._input_image)
        analysis_splitter.addWidget(self._results)
        analysis_splitter.setStretchFactor(0, 4)
        analysis_splitter.setStretchFactor(1, 1)
        analysis_splitter.setSizes([780, 300])
        analysis_layout.addWidget(self._viewer_actions(self._input_image), 0)
        analysis_layout.addWidget(analysis_splitter, 1)
        self._tabs.addTab(analysis_page, "Analyse")

        history_page = QWidget()
        history_layout = QHBoxLayout(history_page)
        history_layout.setContentsMargins(0, 10, 0, 0)
        history_splitter = QSplitter(Qt.Orientation.Horizontal)
        history_list_widget = QWidget()
        history_list_layout = QVBoxLayout(history_list_widget)
        history_list_layout.setContentsMargins(0, 0, 0, 0)
        history_list_layout.setSpacing(8)
        self._history = QListWidget()
        self._history.setMinimumWidth(200)
        self._history.currentItemChanged.connect(self._show_history_item)
        history_list_layout.addWidget(self._history, 1)
        self._open_folder = QPushButton("Ouvrir le dossier")
        self._open_folder.clicked.connect(self._open_history_folder)
        history_list_layout.addWidget(self._open_folder)
        history_splitter.addWidget(history_list_widget)

        history_center = QWidget()
        history_center_layout = QVBoxLayout(history_center)
        history_center_layout.setContentsMargins(10, 0, 10, 0)
        self._image = ImageViewer("Sélectionnez une analyse dans l’historique.")
        self._history_metadata = QLabel("Aucune analyse sélectionnée")
        self._history_metadata.setProperty("secondary", True)
        history_center_layout.addWidget(self._history_metadata)
        history_center_layout.addWidget(self._image, 1)
        history_center_layout.addWidget(self._viewer_actions(self._image))
        history_splitter.addWidget(history_center)
        self._history_results = ObjectPanel(self._service)
        self._history_results.selected.connect(self._history_object_selected)
        history_splitter.addWidget(self._history_results)
        history_splitter.setStretchFactor(0, 1)
        history_splitter.setStretchFactor(1, 4)
        history_splitter.setStretchFactor(2, 1)
        history_splitter.setSizes([230, 750, 300])
        history_layout.addWidget(history_splitter)
        self._tabs.addTab(history_page, "Historique")
        root_layout.addWidget(self._tabs, 1)
        root_layout.addWidget(self._build_footer())
        self.setCentralWidget(root)
        QShortcut(QKeySequence("F11"), self, self._toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self, self._leave_fullscreen)

    def _brand_corner(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 0, 10, 0)
        icon_label = QLabel()
        icon_path = self._paths.icon
        if icon_path.is_file():
            icon_label.setPixmap(QPixmap(str(icon_path)).scaled(
                28,
                28,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        icon_label.setToolTip("Nuts Vision")
        layout.addWidget(icon_label)
        return widget

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("brandFooter")
        footer.setStyleSheet("#brandFooter { background: #22252A; border-radius: 6px; }")
        footer.setFixedHeight(38)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.addStretch()
        attribution_path = self._paths.icon.parent / "powered by_white.png"
        attribution = QLabel()
        attribution.setAlignment(Qt.AlignmentFlag.AlignCenter)
        attribution.setToolTip("Powered by")
        if attribution_path.is_file():
            pixmap = QPixmap(str(attribution_path))
            attribution.setPixmap(pixmap.scaled(
                180,
                26,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            attribution.setText("Ressource de marque indisponible")
            attribution.setProperty("secondary", True)
        layout.addWidget(attribution)
        layout.addStretch()
        return footer

    @staticmethod
    def _viewer_actions(viewer: ImageViewer) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch()
        fit_button = QPushButton("Ajuster")
        fit_button.setToolTip("Ajuster l’image à la zone")
        fit_button.clicked.connect(viewer.fit_image)
        actual_button = QPushButton("100 %")
        actual_button.setToolTip("Afficher à l’échelle réelle")
        actual_button.clicked.connect(viewer.show_actual_size)
        layout.addWidget(fit_button)
        layout.addWidget(actual_button)
        return widget

    def _set_active_status(self, text: str, kind: str = "normal") -> None:
        self._active_status.setText(text)
        self._active_status.setProperty("kind", kind)
        self._active_status.style().unpolish(self._active_status)
        self._active_status.style().polish(self._active_status)

    def _page_changed(self, index: int) -> None:
        self._update_analyze_enabled()
        if index == 1:
            self._open_folder.setEnabled(self._history.currentItem() is not None)

    def _refresh_models(self, selected_identifier: str | None = None) -> None:
        self._models.clear()
        for model in self._model_registry.list_models():
            self._models.addItem(f"{model.name}  ·  {model.sha256[:8]}", model.identifier)
        if selected_identifier:
            index = self._models.findData(selected_identifier)
            if index >= 0:
                self._models.setCurrentIndex(index)
        self._update_analyze_enabled()

    @staticmethod
    def _local_datetime(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone().strftime("%d/%m/%Y %H:%M:%S")
        except (TypeError, ValueError):
            return value

    @staticmethod
    def _translated_status(status: str) -> str:
        return {"processing": "En cours", "completed": "Terminée", "error": "Erreur"}.get(status, "Inconnu")

    def _refresh_history(self, selected_job_id: str | None = None) -> None:
        self._history.clear()
        selected_item = None
        for job in self._repository.list_jobs():
            label = (
                f"{self._local_datetime(job['started_at'])}  ·  {job['model_name']}\n"
                f"{self._translated_status(job['status'])}  ·  {job['object_count']} objet(s)"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, job["id"])
            self._history.addItem(item)
            if job["id"] == selected_job_id:
                selected_item = item
        if selected_item:
            self._history.setCurrentItem(selected_item)
        self._open_folder.setEnabled(self._history.currentItem() is not None)

    def _select_image(self) -> None:
        if self._thread is not None or self._close_when_finished:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Importer une image",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
        )
        if not filename:
            return
        self._selected_image = Path(filename)
        self._input_preview_path = self._selected_image
        self._active_job_id = None
        self._input_name.setText(f"Image d’entrée : {self._selected_image.name}")
        self._input_image.set_detections([])
        self._input_image.load_path(self._input_preview_path)
        self._results.set_result([], "Aucun résultat pour cette image.")
        self._set_active_status(f"Image chargée · {self._selected_image.name}")
        self._update_analyze_enabled()

    def _add_model_from_file(self) -> None:
        if self._thread is not None or self._close_when_finished:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Ajouter un modèle ONNX", "", "Modèle ONNX (*.onnx)")
        if not filename:
            return
        source = Path(filename)
        try:
            inspection = self._model_registry.inspect_candidate(source)
            names = inspection.class_names
            if names is None:
                entered, accepted = QInputDialog.getText(
                    self,
                    "Mapping des classes",
                    f"Entrez les {inspection.class_count} noms de classes dans l’ordre des identifiants, séparés par des virgules :",
                )
                if not accepted:
                    return
                names = tuple(name.strip() for name in entered.split(","))
            model = self._model_registry.register(source, names)
            self._refresh_models(model.identifier)
            self._set_active_status(f"Modèle ajouté · {model.name}", "success")
        except (ModelCompatibilityError, OSError) as exc:
            self._set_diagnostic(str(exc))
            QMessageBox.warning(
                self,
                "Modèle non compatible",
                "Ce modèle ne peut pas être utilisé. Consultez Diagnostic pour les détails.",
            )

    def _set_diagnostic(self, details: str) -> None:
        self._results.diagnostic_text.setText(details)

    def _update_analyze_enabled(self, *_args) -> None:
        idle = self._thread is None and not self._close_when_finished
        self._analyze.setEnabled(
            idle
            and self._tabs.currentIndex() == 0
            and self._selected_image is not None
            and bool(self._models.currentData())
        )

    def _start_analysis(self) -> None:
        if self._thread is not None or self._close_when_finished or self._tabs.currentIndex() != 0:
            return
        if self._selected_image is None:
            self._set_active_status("Importez une image avant l’analyse.", "warning")
            return
        model_id = self._models.currentData()
        if not model_id:
            self._set_active_status("Aucun modèle compatible", "warning")
            return
        self._active_job_id = None
        self._results.set_result([], "Analyse en cours…")
        self._set_active_status("Analyse en cours…")
        self._progress.show()
        for control in (self._choose_image, self._models, self._add_model, self._confidence):
            control.setEnabled(False)
        self._analyze.setEnabled(False)
        self._thread = QThread(self)
        self._worker = AnalysisWorker(
            self._service,
            ManualImageSource(self._selected_image),
            model_id,
            self._confidence.value() / 100,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._analysis_finished)
        self._worker.failed.connect(self._analysis_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._analysis_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _analysis_finished(self, job_id: str) -> None:
        self._active_job_id = job_id
        self._refresh_history(job_id)
        result = self._repository.get_job(job_id)
        if result is None:
            self._set_active_status("Résultat indisponible", "error")
            self._results.set_result([], "Résultat indisponible.")
            return
        log, objects = result
        try:
            original = self._service.resolve_image(log["image_path"])
        except (OSError, ValueError, TypeError):
            original = None
        self._input_preview_path = original
        self._input_image.load_path(original)
        self._input_image.set_detections(objects)
        self._results.set_result(objects, self._result_summary(log, objects), log.get("error_message") or "")
        if log["status"] == "error":
            self._set_active_status("Analyse échouée", "error")
        elif not objects:
            self._set_active_status("Analyse terminée : aucun objet détecté.", "success")
        else:
            self._set_active_status(f"Analyse terminée · {len(objects)} objet(s)", "success")
        self._update_analyze_enabled()

    def _analysis_failed(self, message: str) -> None:
        self._set_active_status("L’analyse n’a pas pu démarrer.", "error")
        self._results.set_result([], "Échec de l’analyse.", message)
        self._set_diagnostic(message)

    def _analysis_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._progress.hide()
        if self._close_when_finished:
            QTimer.singleShot(0, self.close)
            return
        for control in (self._choose_image, self._models, self._add_model, self._confidence):
            control.setEnabled(True)
        self._update_analyze_enabled()

    @staticmethod
    def _result_summary(log: dict, objects: list[dict]) -> str:
        if log["status"] == "error":
            return "Échec · fichiers disponibles" if log.get("image_path") else "Échec de l’analyse."
        if not objects:
            return "Analyse terminée : aucun objet détecté."
        return f"{len(objects)} objet(s) détecté(s)"

    def _analysis_object_selected(self, obj: dict | None) -> None:
        identity = str(obj["id"]) if obj is not None and obj.get("id") else None
        self._input_image.select_detection(identity)

    def _show_history_item(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            self._preview_path = None
            self._image.load_path(None)
            self._image.set_detections([])
            self._history_results.set_result([], "Aucune analyse sélectionnée.")
            self._history_metadata.setText("Aucune analyse sélectionnée")
            self._open_folder.setEnabled(False)
            return
        job_id = current.data(Qt.ItemDataRole.UserRole)
        result = self._repository.get_job(job_id)
        if result is None:
            self._history_metadata.setText("Analyse indisponible")
            self._history_results.set_result([], "Analyse introuvable.")
            return
        log, objects = result
        try:
            self._preview_path = self._service.resolve_image(log["image_path"])
        except (OSError, ValueError, TypeError):
            self._preview_path = None
        self._image.load_path(self._preview_path, "Image introuvable")
        self._image.set_detections(objects)
        diagnostic = log.get("error_message") or ""
        if self._preview_path is None or not self._preview_path.is_file():
            diagnostic = "\n".join(filter(None, (diagnostic, "Fichier image introuvable.")))
        self._history_results.set_result(objects, self._result_summary(log, objects), diagnostic)
        metadata = (
            f"{self._local_datetime(log['started_at'])} · {log['model_name']} · "
            f"{self._translated_status(log['status'])}"
        )
        if log.get("error_message"):
            metadata += f"\n{log['error_message'].splitlines()[0][:180]}"
        self._history_metadata.setText(metadata)
        self._open_folder.setEnabled(True)

    def _history_object_selected(self, obj: dict | None) -> None:
        identity = str(obj["id"]) if obj is not None and obj.get("id") else None
        self._image.select_detection(identity)

    def _show_preview(self, path: Path) -> None:
        path = Path(path)
        self._preview_path = path if path.is_file() else None
        self._image.load_path(path, "Image introuvable")

    def _update_preview(self) -> None:
        # Viewport resizing only changes the graphics view; decoded pixels stay cached.
        return

    def _open_history_folder(self) -> None:
        item = self._history.currentItem()
        if item is None:
            return
        result = self._repository.get_job(item.data(Qt.ItemDataRole.UserRole))
        if result is None:
            return
        try:
            folder = self._service.resolve_image(result[0]["image_path"]).parent
        except (OSError, ValueError, TypeError):
            folder = self._paths.images
        if folder.is_dir():
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self._leave_fullscreen()
            return
        self._fullscreen_geometry = self.normalGeometry() if self.isMaximized() else self.geometry()
        self._fullscreen_maximized = self.isMaximized()
        self.showFullScreen()

    def _leave_fullscreen(self) -> None:
        if QApplication.activeModalWidget() is not None or not self.isFullScreen():
            return
        self.showNormal()
        if self._fullscreen_geometry is not None:
            self.setGeometry(self._fullscreen_geometry)
        if self._fullscreen_maximized:
            self.showMaximized()
        self._fullscreen_geometry = None

    def closeEvent(self, event) -> None:
        if self._thread is None:
            event.accept()
            return
        event.ignore()
        if self._close_when_finished:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Analyse en cours")
        dialog.setText(
            "Une analyse est en cours. L’inférence ne sera pas annulée. "
            "Vous pouvez rester ou fermer après sa terminaison."
        )
        stay = dialog.addButton("Rester dans l’application", QMessageBox.ButtonRole.RejectRole)
        deferred_close = dialog.addButton("Fermer après l’analyse", QMessageBox.ButtonRole.AcceptRole)
        dialog.setDefaultButton(stay)
        dialog.exec()
        if dialog.clickedButton() == deferred_close:
            self._close_when_finished = True
            for control in (self._choose_image, self._models, self._add_model, self._confidence, self._analyze):
                control.setEnabled(False)
            self._set_active_status("Fermeture après la fin de l’analyse")
            if self._thread is None:
                QTimer.singleShot(0, self.close)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F11:
            self._toggle_fullscreen()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self._leave_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
