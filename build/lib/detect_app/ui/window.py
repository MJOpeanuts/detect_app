from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QListWidget,
    QListWidgetItem,
)

from detect_app.config import AppPaths
from detect_app.persistence.repository import AnalysisRepository
from detect_app.services.analysis import AnalysisService
from detect_app.services.image_source import ManualImageSource
from detect_app.services.models import ModelRegistry, ModelSpec
from detect_app.vision.engine import ModelCompatibilityError


class AnalysisWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, service: AnalysisService, source: ManualImageSource, model_id: str):
        super().__init__()
        self._service = service
        self._source = source
        self._model_id = model_id

    def run(self) -> None:
        try:
            self.finished.emit(self._service.analyze(self._source, self._model_id))
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
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self.setWindowTitle("Nuts Vision Desktop")
        self.setMinimumSize(1080, 700)
        if paths.icon.is_file():
            self.setWindowIcon(QIcon(str(paths.icon)))
        self._build_ui()
        self._refresh_models()
        self._refresh_history()
        if startup_error:
            self._status.setText(f"Modèle initial indisponible : {startup_error}")
        elif self._models.count() == 0:
            self._status.setText("Ajoutez un modèle ONNX compatible pour commencer.")

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel("Nuts Vision Desktop")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self._status = QLabel("Prêt")
        self._status.setWordWrap(True)
        header.addWidget(self._status, 2)
        layout.addLayout(header)

        controls = QHBoxLayout()
        self._models = QComboBox()
        self._models.setMinimumWidth(240)
        self._choose_image = QPushButton("Choisir une image…")
        self._choose_image.clicked.connect(self._select_image)
        self._add_model = QPushButton("Ajouter un modèle ONNX…")
        self._add_model.clicked.connect(self._add_model_from_file)
        self._analyze = QPushButton("Analyser")
        self._analyze.setDefault(True)
        self._analyze.clicked.connect(self._start_analysis)
        controls.addWidget(QLabel("Modèle :"))
        controls.addWidget(self._models, 1)
        controls.addWidget(self._add_model)
        controls.addWidget(self._choose_image)
        controls.addWidget(self._analyze)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._history = QListWidget()
        self._history.setMinimumWidth(230)
        self._history.currentItemChanged.connect(self._show_history_item)
        splitter.addWidget(self._history)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        self._image = QLabel("Choisissez une image pour commencer")
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setMinimumSize(480, 380)
        self._image.setStyleSheet("background: #20242b; color: #e5e7eb; border-radius: 8px;")
        self._image.setScaledContents(False)
        content_layout.addWidget(self._image, 3)

        self._objects = QTableWidget(0, 4)
        self._objects.setHorizontalHeaderLabels(["Classe", "ID", "Confiance", "Boîte (x1, y1, x2, y2)"])
        self._objects.horizontalHeader().setStretchLastSection(True)
        self._objects.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._objects.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        content_layout.addWidget(self._objects, 2)
        splitter.addWidget(content)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

    def _refresh_models(self, selected_identifier: str | None = None) -> None:
        self._models.clear()
        for model in self._model_registry.list_models():
            self._models.addItem(f"{model.name}  ({model.sha256[:8]})", model.identifier)
        if selected_identifier:
            index = self._models.findData(selected_identifier)
            if index >= 0:
                self._models.setCurrentIndex(index)

    def _refresh_history(self, selected_job_id: str | None = None) -> None:
        self._history.clear()
        selected_item = None
        for job in self._repository.list_jobs():
            label = (
                f"{job['started_at'][:19].replace('T', ' ')}  ·  {job['model_name']}\n"
                f"{job['object_count']} objet(s)  ·  {job['status']}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, job["id"])
            self._history.addItem(item)
            if job["id"] == selected_job_id:
                selected_item = item
        if selected_item:
            self._history.setCurrentItem(selected_item)

    def _select_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir une image",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
        )
        if not filename:
            return
        self._selected_image = Path(filename)
        self._show_preview(self._selected_image)
        self._objects.setRowCount(0)
        self._status.setText(f"Image sélectionnée : {self._selected_image.name}")

    def _add_model_from_file(self) -> None:
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
                    f"Entrez les {inspection.class_count} noms de classes dans l'ordre des class_id, séparés par des virgules :",
                )
                if not accepted:
                    return
                names = tuple(name.strip() for name in entered.split(","))
            model = self._model_registry.register(source, names)
            self._refresh_models(model.identifier)
            self._status.setText(f"Modèle ajouté : {model.name}")
        except (ModelCompatibilityError, OSError) as exc:
            QMessageBox.warning(self, "Modèle non compatible", str(exc))

    def _start_analysis(self) -> None:
        if self._thread is not None:
            return
        if self._selected_image is None:
            QMessageBox.information(self, "Image requise", "Choisissez d'abord une image à analyser.")
            return
        model_id = self._models.currentData()
        if not model_id:
            QMessageBox.information(self, "Modèle requis", "Ajoutez ou sélectionnez un modèle ONNX compatible.")
            return
        self._choose_image.setEnabled(False)
        self._add_model.setEnabled(False)
        self._analyze.setEnabled(False)
        self._status.setText("Analyse en cours…")
        self._thread = QThread(self)
        self._worker = AnalysisWorker(self._service, ManualImageSource(self._selected_image), model_id)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._analysis_finished)
        self._worker.failed.connect(self._analysis_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._analysis_thread_finished)
        self._thread.start()

    def _analysis_finished(self, job_id: str) -> None:
        self._refresh_history(job_id)
        result = self._repository.get_job(job_id)
        if result and result[0]["status"] == "error":
            self._status.setText(f"Échec de l'analyse : {result[0]['error_message']}")
        else:
            self._status.setText("Analyse terminée.")

    def _analysis_failed(self, message: str) -> None:
        self._status.setText(f"Erreur : {message}")

    def _analysis_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._choose_image.setEnabled(True)
        self._add_model.setEnabled(True)
        self._analyze.setEnabled(True)

    def _show_history_item(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        job_id = current.data(Qt.ItemDataRole.UserRole)
        result = self._repository.get_job(job_id)
        if result is None:
            return
        log, objects = result
        selected_image = log["annotated_image_path"] if log["status"] == "completed" else log["image_path"]
        self._show_preview(self._service.resolve_image(selected_image))
        self._objects.setRowCount(len(objects))
        for row, obj in enumerate(objects):
            box = (
                f"{obj['x_min']:.1f}, {obj['y_min']:.1f}, "
                f"{obj['x_max']:.1f}, {obj['y_max']:.1f}"
            )
            for column, text in enumerate(
                (obj["class_name"], str(obj["class_id"]), f"{obj['confidence']:.1%}", box)
            ):
                self._objects.setItem(row, column, QTableWidgetItem(text))
        self._status.setText(
            f"{log['status']} · {log['model_name']} · {log['started_at'][:19].replace('T', ' ')}"
        )

    def _show_preview(self, path: Path) -> None:
        if not path.is_file():
            self._image.setPixmap(QPixmap())
            self._image.setText("Image introuvable")
            return
        self._preview_path = path
        self._image.setText("")
        self._update_preview()

    def _update_preview(self) -> None:
        if self._preview_path is None:
            return
        pixmap = QPixmap(str(self._preview_path))
        if not pixmap.isNull():
            self._image.setPixmap(
                pixmap.scaled(
                    self._image.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_preview()
