from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """Fold/unfold panel: a chevron header (no checkbox, no on/off switch) and hidden content."""

    toggled = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("collapsibleSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = QToolButton()
        self.header.setObjectName("collapsibleHeader")
        self.header.setText(title)
        # Checkable only to track the open state; it is rendered as a chevron header.
        self.header.setCheckable(True)
        self.header.setChecked(False)
        self.header.setArrowType(Qt.ArrowType.RightArrow)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setAutoRaise(True)
        self.header.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setAccessibleName(title)
        self.header.toggled.connect(self._set_expanded)
        layout.addWidget(self.header)
        self.content = QPlainTextEdit()
        self.content.setObjectName("collapsibleContent")
        self.content.setReadOnly(True)
        self.content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.content.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.content.setMaximumHeight(150)
        self.content.setMinimumHeight(70)
        self.content.setVisible(False)
        layout.addWidget(self.content)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._sections: dict[str, str] = {}
        self._order: list[str] = []
        self._empty_text = "Aucun détail technique."
        self._render()

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)

    def _set_expanded(self, expanded: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)
        self.toggled.emit(expanded)

    def set_section(self, name: str, text: str | None) -> None:
        """Keep each category separate so one never overwrites another."""
        if text:
            if name not in self._order:
                self._order.append(name)
            self._sections[name] = text
        else:
            self._sections.pop(name, None)
            if name in self._order:
                self._order.remove(name)
        self._render()

    def section(self, name: str) -> str:
        return self._sections.get(name, "")

    def clear(self) -> None:
        self._sections.clear()
        self._order.clear()
        self._render()

    def text(self) -> str:
        return self.content.toPlainText()

    def _render(self) -> None:
        if not self._order:
            self.content.setPlainText(self._empty_text)
            return
        blocks = [f"{name}\n{self._sections[name]}" for name in self._order]
        self.content.setPlainText("\n\n".join(blocks))
