from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from detect_app.ui.collapsible import CollapsibleSection


def input_format(model) -> str:
    size = getattr(model, "input_size", None)
    if not size:
        return "Non renseigné"
    return f"float32 · 1 × 3 × {size} × {size} (RGB, letterbox)"


class ModelsPage(QWidget):
    """Lists registered models; adding and removing are requested from the main window."""

    add_requested = Signal()
    remove_requested = Signal(str)
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._models: dict[str, object] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 10, 0)
        left_layout.setSpacing(8)
        heading = QLabel("Modèles enregistrés")
        heading.setObjectName("sectionTitle")
        left_layout.addWidget(heading)
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.currentItemChanged.connect(self._current_changed)
        left_layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.add_button = QPushButton("Ajouter un modèle ONNX")
        self.add_button.setToolTip("Valider puis copier un modèle ONNX compatible dans le stockage géré")
        self.add_button.clicked.connect(self.add_requested)
        self.remove_button = QPushButton("Retirer…")
        self.remove_button.setToolTip("Retirer le modèle sélectionné de detect_app")
        self.remove_button.clicked.connect(self._remove_clicked)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        left_layout.addLayout(buttons)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 0, 0, 0)
        right_layout.setSpacing(8)
        self.title = QLabel("Aucun modèle sélectionné")
        self.title.setObjectName("sectionTitle")
        right_layout.addWidget(self.title)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("note", "normal")
        self.status.hide()
        right_layout.addWidget(self.status)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.fields: dict[str, QLabel] = {}
        for key, label in (
            ("name", "Nom"),
            ("identifier", "Identifiant"),
            ("sha256", "Empreinte SHA-256"),
            ("classes", "Nombre de classes"),
            ("input", "Format d’entrée"),
            ("path", "Fichier géré"),
        ):
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            caption = QLabel(label)
            caption.setProperty("secondary", True)
            form.addRow(caption, value)
            self.fields[key] = value
        right_layout.addLayout(form)
        mapping_caption = QLabel("Mapping des classes")
        mapping_caption.setProperty("secondary", True)
        right_layout.addWidget(mapping_caption)
        self.mapping = QPlainTextEdit()
        self.mapping.setReadOnly(True)
        self.mapping.setMaximumHeight(160)
        right_layout.addWidget(self.mapping)
        right_layout.addStretch(1)
        self.diagnostic = CollapsibleSection("Diagnostic")
        right_layout.addWidget(self.diagnostic)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([340, 700])
        layout.addWidget(splitter, 1)
        self._show(None)

    def set_models(self, models: list, selected_identifier: str | None = None) -> None:
        previous = selected_identifier or self.current_identifier()
        self._models = {model.identifier: model for model in models}
        self.list.blockSignals(True)
        self.list.clear()
        selected_item = None
        for model in models:
            item = QListWidgetItem(f"{model.name}\n{model.sha256[:12]} · {len(model.class_names)} classe(s)")
            item.setData(Qt.ItemDataRole.UserRole, model.identifier)
            self.list.addItem(item)
            if model.identifier == previous:
                selected_item = item
        if selected_item is None and self.list.count():
            selected_item = self.list.item(0)
        if selected_item is not None:
            self.list.setCurrentItem(selected_item)
        self.list.blockSignals(False)
        self._show(self._models.get(self.current_identifier() or ""))
        self.selection_changed.emit()

    def current_identifier(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def model(self, identifier: str):
        return self._models.get(identifier)

    def set_status(self, text: str, kind: str = "normal") -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.status.setProperty("note", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _current_changed(self, *_args) -> None:
        self._show(self._models.get(self.current_identifier() or ""))
        self.selection_changed.emit()

    def _remove_clicked(self) -> None:
        identifier = self.current_identifier()
        if identifier:
            self.remove_requested.emit(identifier)

    def _show(self, model) -> None:
        if model is None:
            self.title.setText("Aucun modèle sélectionné" if self._models else "Aucun modèle compatible")
            for value in self.fields.values():
                value.setText("—")
                value.setToolTip("")
            self.mapping.setPlainText("")
            return
        self.title.setText(model.name)
        self.fields["name"].setText(model.name)
        self.fields["identifier"].setText(model.identifier)
        self.fields["sha256"].setText(f"{model.sha256[:16]}…")
        self.fields["sha256"].setToolTip(model.sha256)
        self.fields["classes"].setText(str(len(model.class_names)))
        self.fields["input"].setText(input_format(model))
        self.fields["path"].setText(str(getattr(model, "path", "—")))
        self.mapping.setPlainText(
            "\n".join(f"{index} = {name}" for index, name in enumerate(model.class_names))
        )
