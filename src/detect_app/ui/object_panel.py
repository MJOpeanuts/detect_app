from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from detect_app.services.analysis import AnalysisService
from detect_app.ui.collapsible import CollapsibleSection
from detect_app.ui.image_viewer import ImageViewer


class NumericTableWidgetItem(QTableWidgetItem):
    SORT_VALUE_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(self.SORT_VALUE_ROLE)
        right = other.data(self.SORT_VALUE_ROLE)
        if left is not None and right is not None:
            return float(left) < float(right)
        return super().__lt__(other)


class ObjectPanel(QWidget):
    selected = Signal(object)
    ERROR_SECTION = "Erreur"
    OBJECT_SECTION = "Objet sélectionné"

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
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.error_summary = QLabel()
        self.error_summary.setObjectName("errorSummary")
        self.error_summary.setWordWrap(True)
        self.error_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_summary.hide()
        layout.addWidget(self.error_summary)
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
        self.diagnostic = CollapsibleSection("Diagnostic")
        layout.addWidget(self.diagnostic)

    def set_error(self, details: str | None) -> None:
        """Short error summary stays visible; full details live in Diagnostic."""
        details = (details or "").strip()
        self.diagnostic.set_section(self.ERROR_SECTION, details)
        if details:
            first_line = details.splitlines()[0]
            self.error_summary.setText(first_line[:200] + ("…" if len(first_line) > 200 else ""))
            self.error_summary.show()
        else:
            self.error_summary.clear()
            self.error_summary.hide()

    def set_result(self, objects: list[dict], summary: str, diagnostic: str = "") -> None:
        self._objects = {str(obj.get("id", index)): obj for index, obj in enumerate(objects)}
        self._selected_id = None
        self.summary.setText(summary)
        self.set_error(diagnostic)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row, (identity, obj) in enumerate(self._objects.items()):
            self.table.insertRow(row)
            name_item = QTableWidgetItem(str(obj.get("class_name", "Objet")))
            name_item.setData(Qt.ItemDataRole.UserRole, identity)
            confidence = float(obj.get("confidence", 0))
            confidence_item = NumericTableWidgetItem(f"{confidence:.1%}")
            confidence_item.setData(Qt.ItemDataRole.UserRole, identity)
            confidence_item.setData(NumericTableWidgetItem.SORT_VALUE_ROLE, confidence)
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
            self.diagnostic.set_section(self.OBJECT_SECTION, None)
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
        self.diagnostic.set_section(self.OBJECT_SECTION, details)

    def selected_id(self) -> str | None:
        return self._selected_id
