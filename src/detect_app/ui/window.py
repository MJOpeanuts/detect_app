from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from detect_app.config import AppPaths
from detect_app.persistence.repository import UNASSIGNED, AnalysisRepository
from detect_app.services.analysis import AnalysisService
from detect_app.services.image_source import ManualImageSource, check_local_image_file
from detect_app.services.models import ModelRegistry
from detect_app.vision.image import SUPPORTED_IMAGE_SUFFIXES

from detect_app.ui.classification import (
    ClassificationDialog,
    classification_path,
    create_client_interactively,
    create_pcba_interactively,
    pcba_label,
)
from detect_app.ui.image_viewer import ImageViewer, PreviewProbe
from detect_app.ui.models_page import ModelsPage
from detect_app.ui.object_panel import ObjectPanel
from detect_app.ui.tasks import TaskRunner
from detect_app.ui.theme import APP_STYLESHEET

ANALYSIS_TAB, HISTORY_TAB, MODELS_TAB = range(3)
ORIGINAL, ANNOTATED = "original", "annotated"


class AnalysisWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        service: AnalysisService,
        source: ManualImageSource,
        model_id: str,
        confidence_threshold: float,
        pcba_id: str | None = None,
    ):
        super().__init__()
        self._service = service
        self._source = source
        self._model_id = model_id
        self._confidence_threshold = confidence_threshold
        self._pcba_id = pcba_id

    def run(self) -> None:
        try:
            self.finished.emit(
                self._service.analyze(
                    self._source,
                    self._model_id,
                    self._confidence_threshold,
                    pcba_id=self._pcba_id,
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
        self._startup_error = startup_error
        self._selected_image: Path | None = None
        self._preview_path: Path | None = None
        self._input_preview_path: Path | None = None
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self._close_when_finished = False
        self._fullscreen_geometry = None
        self._fullscreen_maximized = False
        self._active_job_id: str | None = None
        self._running_model_id: str | None = None
        self._models_loaded = False
        self._model_specs: list = []
        self._history_job: dict | None = None
        self._history_objects: list[dict] = []
        self._history_paths: dict[str, Path | None] = {ORIGINAL: None, ANNOTATED: None}
        self._history_mode = ORIGINAL
        self.setWindowTitle("detect_app")
        self.setMinimumSize(1024, 640)
        icon_file = paths.icon.parent / "nuts-app.ico"
        if not icon_file.is_file():
            icon_file = paths.icon
        if icon_file.is_file():
            self.setWindowIcon(QIcon(str(icon_file)))
        application = QApplication.instance()
        if application is not None:
            application.setStyleSheet(APP_STYLESHEET)
        self.setStyleSheet(APP_STYLESHEET)
        self._tasks = TaskRunner(self)
        self._tasks.finished.connect(self._task_finished)
        self._import_probe = PreviewProbe(self)
        self._import_message_timer = QTimer(self)
        self._import_message_timer.setSingleShot(True)
        self._import_message_timer.setInterval(8000)
        self._import_message_timer.timeout.connect(self._clear_import_message)
        self._import_probe.finished.connect(self._import_probe_finished)
        self._build_ui()
        self._refresh_classification()
        self._refresh_history()
        self._set_active_status("Chargement des modèles…")
        self._refresh_models()
        self._update_controls()

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 12, 16, 6)
        root_layout.setSpacing(6)
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setDrawBase(False)
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
        self._tabs.addTab(self._build_analysis_page(), "Analyse")
        self._tabs.addTab(self._build_history_page(), "Historique")
        self._models_page = ModelsPage()
        self._models_page.add_requested.connect(self._add_model_from_file)
        self._models_page.remove_requested.connect(self._remove_model)
        self._models_page.selection_changed.connect(self._update_controls)
        self._tabs.addTab(self._models_page, "Modèles")
        self._tabs.currentChanged.connect(self._page_changed)
        root_layout.addWidget(self._tabs, 1)
        self._footer = self._build_footer()
        root_layout.addWidget(self._footer)
        self.setCentralWidget(root)
        QShortcut(QKeySequence("F11"), self, self._toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self, self._leave_fullscreen)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("secondary", True)
        return label

    def _build_analysis_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        # Row 1 — source.
        source_row = QHBoxLayout()
        source_row.setSpacing(10)
        self._choose_image = QPushButton("Importer une image")
        self._choose_image.clicked.connect(self._select_image)
        self._input_name = QLabel("Aucune image sélectionnée")
        self._input_name.setObjectName("inputName")
        self._input_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._source_note = QLabel()
        self._source_note.setProperty("note", "hint")
        self._source_note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        source_row.addWidget(self._choose_image)
        source_row.addWidget(self._input_name, 1)
        source_row.addWidget(self._source_note)
        layout.addLayout(source_row)

        # Row 2 — inference parameters, Analyser on the right.
        parameters_row = QHBoxLayout()
        parameters_row.setSpacing(8)
        self._models = QComboBox()
        self._models.setMinimumWidth(200)
        self._models.setMaximumWidth(340)
        self._models.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._models.currentIndexChanged.connect(self._update_controls)
        self._confidence = QDoubleSpinBox()
        self._confidence.setRange(0, 100)
        self._confidence.setDecimals(1)
        self._confidence.setSingleStep(0.5)
        self._confidence.setValue(25)
        self._confidence.setSuffix(" %")
        self._confidence.setKeyboardTracking(False)
        self._confidence.setFixedWidth(105)
        self._no_model_hint = QLabel(
            'Aucun modèle compatible · <a href="models" style="color:#DDE1E6;">ouvrir Modèles</a>'
        )
        self._no_model_hint.setProperty("note", "warning")
        self._no_model_hint.setTextFormat(Qt.TextFormat.RichText)
        self._no_model_hint.linkActivated.connect(lambda _link: self._tabs.setCurrentIndex(MODELS_TAB))
        self._no_model_hint.hide()
        self._analyze = QPushButton("Analyser")
        self._analyze.setObjectName("analyzeButton")
        squirrel_icon = self._paths.icon.parent / "squirrel.svg"
        if squirrel_icon.is_file():
            self._analyze.setIcon(QIcon(str(squirrel_icon)))
        self._analyze.setDefault(False)
        self._analyze.clicked.connect(self._start_analysis)
        parameters_row.addWidget(QLabel("Modèle"))
        parameters_row.addWidget(self._models, 1)
        parameters_row.addSpacing(12)
        parameters_row.addWidget(QLabel("Confiance minimale"))
        parameters_row.addWidget(self._confidence)
        parameters_row.addStretch(1)
        parameters_row.addWidget(self._no_model_hint)
        parameters_row.addSpacing(8)
        parameters_row.addWidget(self._analyze)
        layout.addLayout(parameters_row)

        # Row 3 — optional classification, separate from inference parameters.
        classification_row = QHBoxLayout()
        classification_row.setSpacing(8)
        classification_title = QLabel("Classement facultatif")
        classification_title.setObjectName("rowTitle")
        self._client_combo = QComboBox()
        self._client_combo.setMinimumWidth(150)
        self._client_combo.setMaximumWidth(240)
        self._client_combo.currentIndexChanged.connect(self._analysis_client_changed)
        self._new_client = QPushButton("Nouveau client…")
        self._new_client.clicked.connect(self._create_client)
        self._pcba_combo = QComboBox()
        self._pcba_combo.setMinimumWidth(170)
        self._pcba_combo.setMaximumWidth(280)
        self._pcba_combo.currentIndexChanged.connect(self._analysis_pcba_changed)
        self._new_pcba = QPushButton("Nouveau PCBA…")
        self._new_pcba.clicked.connect(self._create_pcba)
        self._classification_hint = QLabel()
        self._classification_hint.setProperty("secondary", True)
        classification_row.addWidget(classification_title)
        classification_row.addSpacing(6)
        classification_row.addWidget(QLabel("Client"))
        classification_row.addWidget(self._client_combo, 1)
        classification_row.addWidget(self._new_client)
        classification_row.addSpacing(12)
        classification_row.addWidget(QLabel("PCBA"))
        classification_row.addWidget(self._pcba_combo, 1)
        classification_row.addWidget(self._new_pcba)
        classification_row.addSpacing(8)
        classification_row.addWidget(self._classification_hint, 1)
        layout.addLayout(classification_row)

        self._input_image = ImageViewer("Importez une image ou glissez-la ici.")
        self._input_image.set_drop_handlers(self._drop_is_acceptable, self._drop_image)
        self._input_state = QLabel()
        self._input_state.setObjectName("viewerState")
        layout.addWidget(self._viewer_actions(self._input_image, self._input_state), 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._results = ObjectPanel(self._service)
        self._results.selected.connect(self._analysis_object_selected)
        splitter.addWidget(self._input_image)
        splitter.addWidget(self._results)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([860, 320])
        layout.addWidget(splitter, 1)
        return page

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 10, 0)
        list_layout.setSpacing(8)
        filters = QGridLayout()
        filters.setHorizontalSpacing(8)
        filters.setVerticalSpacing(6)
        self._history_client_filter = QComboBox()
        self._history_client_filter.currentIndexChanged.connect(self._history_client_filter_changed)
        self._history_pcba_filter = QComboBox()
        self._history_pcba_filter.currentIndexChanged.connect(lambda _index: self._refresh_history())
        filters.addWidget(self._caption("Client"), 0, 0)
        filters.addWidget(self._history_client_filter, 0, 1)
        filters.addWidget(self._caption("PCBA"), 1, 0)
        filters.addWidget(self._history_pcba_filter, 1, 1)
        filters.setColumnStretch(1, 1)
        list_layout.addLayout(filters)
        self._history = QListWidget()
        self._history.setMinimumWidth(240)
        self._history.currentItemChanged.connect(self._show_history_item)
        list_layout.addWidget(self._history, 1)
        history_buttons = QHBoxLayout()
        history_buttons.setSpacing(8)
        self._open_folder = QPushButton("Ouvrir le dossier")
        self._open_folder.clicked.connect(self._open_history_folder)
        self._edit_classification = QPushButton("Modifier le classement")
        self._edit_classification.clicked.connect(self._edit_history_classification)
        history_buttons.addWidget(self._open_folder)
        history_buttons.addWidget(self._edit_classification)
        history_buttons.addStretch()
        list_layout.addLayout(history_buttons)
        splitter.addWidget(list_widget)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(10, 0, 10, 0)
        center_layout.setSpacing(6)
        self._history_metadata = QLabel("Aucune analyse sélectionnée")
        self._history_metadata.setProperty("secondary", True)
        self._history_metadata.setWordWrap(True)
        self._history_metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        center_layout.addWidget(self._history_metadata)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_buttons: dict[str, QPushButton] = {}
        for mode, text, position in ((ORIGINAL, "Original", "left"), (ANNOTATED, "Annotée", "right")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setProperty("segment", position)
            button.clicked.connect(lambda _checked=False, value=mode: self._set_history_mode(value))
            self._mode_group.addButton(button)
            self._mode_buttons[mode] = button
            mode_row.addWidget(button)
        mode_row.addSpacing(12)
        self._history_warning = QLabel()
        self._history_warning.setProperty("note", "warning")
        self._history_warning.setWordWrap(True)
        mode_row.addWidget(self._history_warning, 1)
        center_layout.addLayout(mode_row)
        self._image = ImageViewer("Sélectionnez une analyse dans l’historique.")
        center_layout.addWidget(self._image, 1)
        center_layout.addWidget(self._viewer_actions(self._image))
        splitter.addWidget(center)
        self._history_results = ObjectPanel(self._service)
        self._history_results.selected.connect(self._history_object_selected)
        splitter.addWidget(self._history_results)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([270, 700, 300])
        layout.addWidget(splitter)
        return page

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
        icon_label.setToolTip("detect_app")
        layout.addWidget(icon_label)
        return widget

    def _build_footer(self) -> QWidget:
        # The brand PNG is drawn directly on the window background: no extra surface,
        # border, radius or shadow; the resource itself is only scaled.
        footer = QWidget()
        footer.setObjectName("brandFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        footer.setFixedHeight(28)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)
        layout.addStretch()
        attribution_path = self._paths.icon.parent / "powered by_white.png"
        attribution = QLabel()
        attribution.setObjectName("brandAttribution")
        attribution.setAlignment(Qt.AlignmentFlag.AlignCenter)
        attribution.setToolTip("Powered by")
        if attribution_path.is_file():
            pixmap = QPixmap(str(attribution_path))
            ratio = max(1.0, self.devicePixelRatioF())
            scaled = pixmap.scaledToHeight(round(20 * ratio), Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            attribution.setPixmap(scaled)
        else:
            attribution.setText("Ressource de marque indisponible")
            attribution.setProperty("secondary", True)
        layout.addWidget(attribution)
        layout.addStretch()
        return footer

    @staticmethod
    def _viewer_actions(viewer: ImageViewer, state: QLabel | None = None) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if state is not None:
            layout.addWidget(state)
        layout.addStretch()
        fit_button = QPushButton("Ajuster")
        fit_button.setToolTip("Ajuster l’image à la zone")
        fit_button.clicked.connect(viewer.fit_image)
        actual_button = QPushButton("100 %")
        actual_button.setToolTip(
            "Vise l’échelle d’un pixel original ; le détail reste limité si l’aperçu a été réduit."
        )
        actual_button.clicked.connect(viewer.show_actual_size)
        layout.addWidget(fit_button)
        layout.addWidget(actual_button)
        return widget

    # ------------------------------------------------------------------ state

    def _set_active_status(self, text: str, kind: str = "normal") -> None:
        self._active_status.setText(text)
        self._active_status.setProperty("kind", kind)
        self._active_status.style().unpolish(self._active_status)
        self._active_status.style().polish(self._active_status)

    @staticmethod
    def _set_note(label: QLabel, text: str, kind: str) -> None:
        label.setText(text)
        label.setProperty("note", kind)
        label.style().unpolish(label)
        label.style().polish(label)

    def _busy(self) -> bool:
        return self._thread is not None or self._close_when_finished

    def _page_changed(self, _index: int) -> None:
        self._update_controls()

    def _update_controls(self, *_args) -> None:
        busy = self._busy()
        for control in (
            self._choose_image,
            self._models,
            self._confidence,
            self._client_combo,
            self._pcba_combo,
            self._new_client,
            self._new_pcba,
        ):
            control.setEnabled(not busy)
        self._update_analyze_enabled()
        self._no_model_hint.setVisible(self._models_loaded and self._models.count() == 0)
        if not self._source_note.property("message"):
            if busy:
                self._set_note(self._source_note, "", "hint")
            elif self._import_probe.pending:
                self._set_note(self._source_note, "Vérification de l’image…", "hint")
            else:
                hint = "ou glissez une autre image sur la zone d’affichage" if self._selected_image else (
                    "ou glissez une image sur la zone d’affichage"
                )
                self._set_note(self._source_note, hint, "hint")
        job = self._history_job
        self._open_folder.setEnabled(job is not None)
        self._edit_classification.setEnabled(
            job is not None and job.get("status") != "processing" and not self._close_when_finished
        )
        model_task = self._tasks.is_pending()
        self._models_page.add_button.setEnabled(not self._close_when_finished and not model_task)
        self._models_page.remove_button.setEnabled(
            not self._close_when_finished and not model_task and self._models_page.current_identifier() is not None
        )

    def _update_analyze_enabled(self, *_args) -> None:
        idle = self._thread is None and not self._close_when_finished
        self._analyze.setEnabled(
            idle
            and self._tabs.currentIndex() == ANALYSIS_TAB
            and self._selected_image is not None
            and not self._import_probe.pending
            and bool(self._models.currentData())
        )

    # ------------------------------------------------------------------ models

    def _refresh_models(self, selected_identifier: str | None = None) -> None:
        registry = self._model_registry
        self._tasks.run("list", lambda: (registry.list_models(), selected_identifier))
        self._update_controls()

    def _models_listed(self, models: list, selected_identifier: str | None) -> None:
        first_load = not self._models_loaded
        self._models_loaded = True
        self._model_specs = list(models)
        previous = selected_identifier or self._models.currentData()
        self._models.blockSignals(True)
        self._models.clear()
        for model in models:
            self._models.addItem(f"{model.name}  ·  {model.sha256[:8]}", model.identifier)
        index = self._models.findData(previous) if previous else -1
        self._models.setCurrentIndex(index if index >= 0 else (0 if self._models.count() else -1))
        self._models.blockSignals(False)
        self._models_page.set_models(models, selected_identifier)
        if first_load and not self._busy():
            if self._startup_error:
                self._set_active_status("Modèle initial indisponible", "warning")
                self._results.set_result([], "Aucun résultat", self._startup_error)
                self._models_page.diagnostic.set_section("Modèle initial", self._startup_error)
            elif not models:
                self._set_active_status("Aucun modèle compatible", "warning")
                self._results.set_result([], "Ajoutez un modèle dans l’onglet Modèles.")
            elif self._active_status.text() == "Chargement des modèles…":
                self._set_active_status("Aucune image sélectionnée")
        self._update_controls()

    def _task_finished(self, name: str, result: object, error: object) -> None:
        if name == "list":
            if error is not None:
                self._models_listed([], None)
                self._models_page.set_status("Liste des modèles indisponible.", "error")
                self._models_page.diagnostic.set_section("Liste des modèles", str(error))
                return
            models, selected = result
            self._models_listed(models, selected)
        elif name == "inspect":
            self._model_inspected(result, error)
        elif name == "register":
            self._model_registered(result, error)
        elif name == "remove":
            self._model_removed(result, error)
        self._update_controls()

    def _model_error(self, title: str, error: object) -> None:
        details = str(error) or error.__class__.__name__
        self._models_page.set_status(f"{title} : {details.splitlines()[0][:160]}", "error")
        self._models_page.diagnostic.set_section(title, details)
        QMessageBox.warning(self, title, f"{details.splitlines()[0][:300]}\n\nDétails dans Diagnostic.")

    def _add_model_from_file(self) -> None:
        if self._close_when_finished or self._tasks.is_pending():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Ajouter un modèle ONNX",
            "",
            "Modèle ONNX (*.onnx)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not filename:
            return
        source = Path(filename)
        registry = self._model_registry
        self._models_page.set_status(f"Vérification de {source.name}…")
        self._models_page.diagnostic.set_section("Ajout", None)
        self._tasks.run("inspect", lambda: (source, registry.inspect_candidate(source)))
        self._update_controls()

    def _model_inspected(self, result, error) -> None:
        if error is not None:
            self._model_error("Modèle non compatible", error)
            return
        source, inspection = result
        names = inspection.class_names
        if names is None:
            entered, accepted = QInputDialog.getText(
                self,
                "Mapping des classes",
                f"Entrez les {inspection.class_count} noms de classes dans l’ordre des identifiants, "
                "séparés par des virgules :",
            )
            if not accepted:
                self._models_page.set_status("Ajout annulé.")
                return
            names = tuple(name.strip() for name in entered.split(","))
        registry = self._model_registry
        self._models_page.set_status(f"Copie et empreinte de {source.name}…")
        self._tasks.run("register", lambda: registry.register(source, names))

    def _model_registered(self, model, error) -> None:
        if error is not None:
            self._model_error("Modèle non compatible", error)
            return
        self._models_page.set_status(f"Modèle ajouté · {model.name}", "success")
        self._refresh_models(model.identifier)

    def _remove_model(self, identifier: str) -> None:
        if self._close_when_finished or self._tasks.is_pending():
            return
        model = self._models_page.model(identifier)
        if model is None:
            return
        if self._thread is not None and self._running_model_id == identifier:
            QMessageBox.information(
                self,
                "Retrait impossible",
                "Ce modèle est utilisé par l’analyse en cours. Réessayez après sa fin.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Retirer le modèle",
            f"Retirer « {model.name} » ({model.sha256[:12]}) de detect_app ?\n\n"
            f"Seront retirés : la copie gérée par l’application\n{model.path}\n"
            "et sa configuration de mapping des classes.\n\n"
            "Conservés : le fichier ONNX que vous aviez fourni, toutes les analyses "
            "de l’historique et leurs fichiers de résultats.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        registry = self._model_registry
        self._models_page.set_status(f"Retrait de {model.name}…")
        self._tasks.run("remove", lambda: (model, registry.remove(identifier)))
        self._update_controls()

    def _model_removed(self, result, error) -> None:
        if error is not None:
            self._model_error("Retrait impossible", error)
            return
        model, _removed = result
        self._models_page.set_status(f"Modèle retiré · {model.name}. Historique conservé.", "success")
        self._refresh_models()

    # ------------------------------------------------------------------ classification

    def _refresh_classification(self) -> None:
        self._fill_analysis_classification(self._client_combo.currentData(), self._pcba_combo.currentData())
        self._fill_history_filters()

    def _fill_analysis_classification(self, client_id: str | None, pcba_id: str | None) -> None:
        pcba = self._repository.get_pcba(pcba_id) if pcba_id else None
        if pcba is not None:
            client_id = pcba["client_id"]
        self._client_combo.blockSignals(True)
        self._client_combo.clear()
        self._client_combo.addItem("Aucun client", None)
        for client in self._repository.list_clients():
            self._client_combo.addItem(client["name"], client["id"])
        index = self._client_combo.findData(client_id) if client_id else 0
        self._client_combo.setCurrentIndex(max(0, index))
        self._client_combo.blockSignals(False)
        self._fill_analysis_pcbas(pcba["id"] if pcba else None)

    def _fill_analysis_pcbas(self, pcba_id: str | None) -> None:
        client_id = self._client_combo.currentData()
        self._pcba_combo.blockSignals(True)
        self._pcba_combo.clear()
        self._pcba_combo.addItem("Aucun PCBA", None)
        for pcba in self._repository.list_pcbas(client_id):
            self._pcba_combo.addItem(pcba_label(pcba, with_client=client_id is None), pcba["id"])
        index = self._pcba_combo.findData(pcba_id) if pcba_id else 0
        self._pcba_combo.setCurrentIndex(max(0, index))
        self._pcba_combo.blockSignals(False)
        self._update_classification_hint()

    def _analysis_client_changed(self, _index: int) -> None:
        current = self._pcba_combo.currentData()
        pcba = self._repository.get_pcba(current) if current else None
        client_id = self._client_combo.currentData()
        keep = pcba is not None and (client_id is None or pcba["client_id"] == client_id)
        self._fill_analysis_pcbas(current if keep else None)

    def _analysis_pcba_changed(self, _index: int) -> None:
        pcba_id = self._pcba_combo.currentData()
        pcba = self._repository.get_pcba(pcba_id) if pcba_id else None
        if pcba is not None and pcba["client_id"] and pcba["client_id"] != self._client_combo.currentData():
            # Show the PCBA's own client; never move the PCBA to another client here.
            self._fill_analysis_classification(pcba["client_id"], pcba_id)
            return
        self._update_classification_hint()

    def _update_classification_hint(self) -> None:
        pcba_id = self._pcba_combo.currentData()
        if pcba_id:
            pcba = self._repository.get_pcba(pcba_id)
            text = f"Client : {pcba['client_name']}" if pcba and pcba["client_name"] else "PCBA sans client"
        elif self._client_combo.currentData():
            text = "Sans PCBA, le job restera non classé."
        else:
            text = ""
        self._classification_hint.setText(text)

    def _create_client(self) -> None:
        if self._busy():
            return
        created = create_client_interactively(self, self._repository)
        if created:
            self._fill_analysis_classification(created["id"], None)
            self._fill_history_filters()

    def _create_pcba(self) -> None:
        if self._busy():
            return
        created = create_pcba_interactively(self, self._repository, self._client_combo.currentData())
        if created:
            self._fill_analysis_classification(created["client_id"], created["id"])
            self._fill_history_filters()

    def _fill_history_filters(self) -> None:
        client_filter = self._history_client_filter.currentData() if self._history_client_filter.count() else None
        self._history_client_filter.blockSignals(True)
        self._history_client_filter.clear()
        self._history_client_filter.addItem("Tous", None)
        self._history_client_filter.addItem("Sans client", UNASSIGNED)
        for client in self._repository.list_clients():
            self._history_client_filter.addItem(client["name"], client["id"])
        index = self._history_client_filter.findData(client_filter) if client_filter is not None else 0
        self._history_client_filter.setCurrentIndex(max(0, index))
        self._history_client_filter.blockSignals(False)
        self._fill_history_pcba_filter()

    def _fill_history_pcba_filter(self) -> None:
        client_filter = self._history_client_filter.currentData()
        previous = self._history_pcba_filter.currentData() if self._history_pcba_filter.count() else None
        self._history_pcba_filter.blockSignals(True)
        self._history_pcba_filter.clear()
        self._history_pcba_filter.addItem("Tous", None)
        if client_filter is None or client_filter == UNASSIGNED:
            # A job without PCBA never has a client, so this choice is offered only when coherent.
            self._history_pcba_filter.addItem("Sans PCBA", UNASSIGNED)
        for pcba in self._repository.list_pcbas(client_filter):
            self._history_pcba_filter.addItem(pcba_label(pcba, with_client=client_filter is None), pcba["id"])
        index = self._history_pcba_filter.findData(previous) if previous is not None else 0
        self._history_pcba_filter.setCurrentIndex(max(0, index))
        self._history_pcba_filter.blockSignals(False)

    def _history_client_filter_changed(self, _index: int) -> None:
        self._fill_history_pcba_filter()
        self._refresh_history(self._history_job["id"] if self._history_job else None)

    def _edit_history_classification(self) -> None:
        job = self._history_job
        if job is None or self._close_when_finished:
            return
        fresh = self._repository.get_job(job["id"])
        if fresh is None or fresh[0]["status"] == "processing":
            QMessageBox.information(
                self, "Classement verrouillé", "Le classement d’une analyse en cours ne peut pas être modifié."
            )
            return
        dialog = ClassificationDialog(self._repository, fresh[0], self)
        dialog.exec()
        if dialog.changed:
            self._fill_analysis_classification(self._client_combo.currentData(), self._pcba_combo.currentData())
            self._fill_history_filters()
            self._refresh_history(job["id"])

    # ------------------------------------------------------------------ history

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

    def _history_filters(self) -> tuple[str | None, str | None]:
        client_filter = self._history_client_filter.currentData() if self._history_client_filter.count() else None
        pcba_filter = self._history_pcba_filter.currentData() if self._history_pcba_filter.count() else None
        return client_filter, pcba_filter

    def _refresh_history(self, selected_job_id: str | None = None) -> None:
        if selected_job_id is None and self._history_job is not None:
            selected_job_id = self._history_job["id"]
        client_filter, pcba_filter = self._history_filters()
        self._history.blockSignals(True)
        self._history.clear()
        selected_item = None
        for job in self._repository.list_jobs(client_filter, pcba_filter):
            label = (
                f"{self._local_datetime(job['started_at'])}  ·  {job['model_name']}\n"
                f"{classification_path(job, include_job=False)}\n"
                f"{self._translated_status(job['status'])}  ·  {job['object_count']} objet(s)"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, job["id"])
            self._history.addItem(item)
            if job["id"] == selected_job_id:
                selected_item = item
        if selected_item is not None:
            self._history.setCurrentItem(selected_item)
        self._history.blockSignals(False)
        self._show_history_item(selected_item, None)

    def _resolve(self, relative: str | None) -> Path | None:
        if not relative:
            return None
        try:
            return self._service.resolve_image(relative)
        except (OSError, ValueError, TypeError):
            return None

    def _show_history_item(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            self._history_job = None
            self._history_objects = []
            self._history_paths = {ORIGINAL: None, ANNOTATED: None}
            self._preview_path = None
            self._image.load_path(None)
            self._image.set_detections([])
            self._history_results.set_result([], "Aucune analyse sélectionnée.")
            self._history_metadata.setText("Aucune analyse sélectionnée")
            self._set_note(self._history_warning, "", "warning")
            for button in self._mode_buttons.values():
                button.setEnabled(False)
            self._update_controls()
            return
        job_id = current.data(Qt.ItemDataRole.UserRole)
        result = self._repository.get_job(job_id)
        if result is None:
            self._history_job = None
            self._history_metadata.setText("Analyse indisponible")
            self._history_results.set_result([], "Analyse introuvable.")
            self._update_controls()
            return
        log, objects = result
        same_job = self._history_job is not None and self._history_job["id"] == log["id"]
        self._history_job = log
        self._history_objects = objects
        original = self._resolve(log["image_path"])
        annotated = self._resolve(log.get("annotated_image_path"))
        self._history_paths = {
            ORIGINAL: original if original is not None and original.is_file() else None,
            ANNOTATED: annotated if annotated is not None and annotated.is_file() else None,
        }
        warnings = []
        if self._history_paths[ORIGINAL] is None:
            warnings.append("Image d’origine introuvable.")
        if log["status"] == "completed" and self._history_paths[ANNOTATED] is None:
            warnings.append("Image annotée introuvable : les objets restent listés, sans relancer l’analyse.")
        elif log["status"] == "error":
            warnings.append("Analyse en erreur : aucune image annotée.")
        self._set_note(self._history_warning, " ".join(warnings), "warning")
        self._mode_buttons[ORIGINAL].setEnabled(self._history_paths[ORIGINAL] is not None)
        self._mode_buttons[ANNOTATED].setEnabled(self._history_paths[ANNOTATED] is not None)
        diagnostic = log.get("error_message") or ""
        self._history_results.set_result(objects, self._result_summary(log, objects), diagnostic)
        missing = [
            f"{label} : {path}"
            for label, path, stored in (
                ("Original introuvable", original, log["image_path"]),
                ("Image annotée introuvable", annotated, log.get("annotated_image_path")),
            )
            if stored and (path is None or not path.is_file())
        ]
        self._history_results.diagnostic.set_section("Fichiers", "\n".join(missing) or None)
        self._history_metadata.setText(
            f"{classification_path(log)}\n"
            f"{self._local_datetime(log['started_at'])} · {log['model_name']} · "
            f"{self._translated_status(log['status'])} · {len(objects)} objet(s)"
        )
        if same_job and self._history_paths.get(self._history_mode) is not None:
            mode = self._history_mode
        else:
            mode = ANNOTATED if self._history_paths[ANNOTATED] is not None else ORIGINAL
        self._set_history_mode(mode, keep_view=same_job)
        self._update_controls()

    def _set_history_mode(self, mode: str, keep_view: bool = True) -> None:
        """Switch the viewer between the saved original and annotated files (no inference)."""
        self._history_mode = mode
        for value, button in self._mode_buttons.items():
            button.blockSignals(True)
            button.setChecked(value == mode)
            button.blockSignals(False)
        path = self._history_paths.get(mode)
        self._preview_path = path
        selected = self._history_results.selected_id()
        if mode == ANNOTATED:
            self._image.load_path(path, "Image annotée introuvable", keep_view=keep_view)
            # The annotated file already contains every box: only the selection is overlaid.
            self._image.set_overlay_mode(ImageViewer.OVERLAY_SELECTION)
            self._image.set_detections(self._history_objects, selected)
        else:
            self._image.load_path(path, "Image d’origine introuvable", keep_view=keep_view)
            self._image.set_overlay_mode(ImageViewer.OVERLAY_NONE)
            self._image.set_detections(self._history_objects, selected)

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
        job = self._history_job
        if job is None:
            return
        path = self._resolve(job["image_path"]) or self._resolve(job.get("annotated_image_path"))
        folder = path.parent if path is not None else self._paths.images
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ------------------------------------------------------------------ import

    def _import_blocked_reason(self) -> str | None:
        if self._close_when_finished:
            return "Import indisponible : fermeture en attente de la fin de l’analyse."
        if self._thread is not None:
            return "Import indisponible pendant l’analyse : l’image d’entrée est conservée."
        return None

    @staticmethod
    def _path_from_urls(urls: list[QUrl]) -> tuple[Path | None, str | None]:
        if not urls:
            return None, "Aucun fichier déposé."
        if len(urls) != 1:
            return None, "Déposez une seule image à la fois."
        url = urls[0]
        if not url.isLocalFile():
            return None, "Seuls les fichiers locaux sont acceptés : URL distante refusée."
        return Path(url.toLocalFile()), None

    def _drop_is_acceptable(self, mime: QMimeData) -> bool:
        if self._import_blocked_reason() is not None:
            return False
        path, error = self._path_from_urls(mime.urls())
        return error is None and path is not None and path.is_file() and (
            path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        )

    def _drop_image(self, mime: QMimeData) -> None:
        path, error = self._path_from_urls(mime.urls())
        blocked = self._import_blocked_reason()
        if blocked or error:
            self._import_refused(blocked or error)
            return
        self._request_import(path)

    def _select_image(self) -> None:
        if self._import_blocked_reason() is not None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Importer une image",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not filename:
            return
        self._request_import(Path(filename))

    def _request_import(self, path: Path) -> None:
        """Single validation path for the file dialog and drag-and-drop."""
        blocked = self._import_blocked_reason()
        if blocked:
            self._import_refused(blocked)
            return
        error = check_local_image_file(path)
        if error:
            self._import_refused(f"{path.name or path} : {error}")
            return
        self._import_message_timer.stop()
        self._source_note.setProperty("message", False)
        self._import_probe.request(path)
        self._update_controls()

    def _import_refused(self, message: str) -> None:
        # Local message: the current image, results and processing status stay untouched.
        self._source_note.setProperty("message", True)
        self._set_note(self._source_note, message, "warning")
        self._source_note.setToolTip(message)
        self._import_message_timer.start()
        self._update_controls()

    def _clear_import_message(self) -> None:
        self._source_note.setProperty("message", False)
        self._source_note.setToolTip("")
        self._update_controls()

    def _import_probe_finished(self, path: Path, image, source_size, error: str) -> None:
        blocked = self._import_blocked_reason()
        if blocked:
            self._import_refused(blocked)
            return
        if error or image is None or image.isNull() or source_size is None:
            self._import_refused(f"{path.name} : {error or 'image illisible.'}")
            return
        self._selected_image = path
        self._input_preview_path = path
        self._active_job_id = None
        self._source_note.setProperty("message", False)
        self._input_name.setText(path.name)
        self._input_name.setToolTip(str(path))
        self._input_image.set_overlay_mode(ImageViewer.OVERLAY_ALL)
        self._input_image.show_image(path, image, source_size)
        self._input_state.setText("Image d’origine")
        self._results.set_result([], "Aucun résultat pour cette image.")
        self._set_active_status(f"Image chargée · {path.name}")
        self._update_controls()

    # ------------------------------------------------------------------ analysis

    def _start_analysis(self) -> None:
        if self._busy() or self._tabs.currentIndex() != ANALYSIS_TAB or self._import_probe.pending:
            return
        if self._selected_image is None:
            self._set_active_status("Importez une image avant l’analyse.", "warning")
            return
        model_id = self._models.currentData()
        if not model_id:
            self._set_active_status("Aucun modèle compatible", "warning")
            return
        pcba_id = self._pcba_combo.currentData()
        self._active_job_id = None
        self._running_model_id = model_id
        self._input_image.set_detections([])
        self._results.set_result([], "Analyse en cours…")
        self._set_active_status("Analyse en cours…")
        self._progress.show()
        self._thread = QThread(self)
        self._update_controls()
        self._worker = AnalysisWorker(
            self._service,
            ManualImageSource(self._selected_image),
            model_id,
            self._confidence.value() / 100,
            pcba_id,
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
        original = self._resolve(log["image_path"])
        annotated = self._resolve(log.get("annotated_image_path"))
        if log["status"] == "completed" and annotated is not None and annotated.is_file():
            shown, mode, state = annotated, ImageViewer.OVERLAY_SELECTION, "Image annotée"
        elif log["status"] == "completed":
            shown, mode = original, ImageViewer.OVERLAY_ALL
            state = "Image d’origine · boîtes reconstruites depuis les coordonnées enregistrées"
        else:
            shown, mode, state = original, ImageViewer.OVERLAY_NONE, "Image d’origine"
        self._input_preview_path = shown
        self._input_image.load_path(shown, keep_view=True)
        self._input_image.set_overlay_mode(mode)
        self._input_image.set_detections(objects)
        self._input_state.setText(state)
        self._results.set_result(objects, self._result_summary(log, objects), log.get("error_message") or "")
        if log["status"] == "error":
            self._set_active_status("Analyse échouée", "error")
        elif not objects:
            self._set_active_status("Analyse terminée : aucun objet détecté.", "success")
        else:
            self._set_active_status(f"Analyse terminée · {len(objects)} objet(s)", "success")
        self._update_controls()

    def _analysis_failed(self, message: str) -> None:
        self._input_image.set_detections([])
        self._set_active_status("L’analyse n’a pas pu démarrer.", "error")
        self._results.set_result([], "Échec de l’analyse.", message)

    def _analysis_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._running_model_id = None
        self._progress.hide()
        if self._close_when_finished:
            QTimer.singleShot(0, self.close)
            return
        self._update_controls()

    @staticmethod
    def _result_summary(log: dict, objects: list[dict]) -> str:
        if log["status"] == "error":
            return "Échec de l’analyse."
        if log["status"] == "processing":
            return "Analyse en cours…"
        if not objects:
            return "Analyse terminée : aucun objet détecté."
        return f"{len(objects)} objet(s) détecté(s)"

    def _analysis_object_selected(self, obj: dict | None) -> None:
        identity = str(obj["id"]) if obj is not None and obj.get("id") else None
        self._input_image.select_detection(identity)

    # ------------------------------------------------------------------ window

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
            self._import_probe.cancel()
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
            self._import_probe.cancel()
            self._update_controls()
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
