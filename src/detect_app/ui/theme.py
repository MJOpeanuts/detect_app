from PySide6.QtGui import QColor


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
QProgressBar::chunk { background: #A9B0BA; border-radius: 2px; }
QScrollBar:vertical { background: #181A1D; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #3A4048; border-radius: 4px; min-height: 20px; }
QScrollBar:horizontal { background: #181A1D; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #3A4048; border-radius: 4px; min-width: 20px; }
QToolTip { color: #F2F3F5; background: #22252A; border: 1px solid #3A4048; }
QMessageBox, QFileDialog, QInputDialog, QDialog { background: #181A1D; }
QLabel#sectionTitle { font-size: 11pt; font-weight: 600; }
QLabel#rowTitle { color: #A9B0BA; font-weight: 600; }
QLabel#inputName { color: #F2F3F5; }
QLabel#viewerState { color: #A9B0BA; font-weight: 600; }
QLabel#errorSummary { color: #F18B86; }
QLabel[note="hint"] { color: #A9B0BA; }
QLabel[note="normal"] { color: #A9B0BA; }
QLabel[note="success"] { color: #83D6A3; }
QLabel[note="warning"] { color: #E9C46A; }
QLabel[note="error"] { color: #F18B86; }
QWidget#brandFooter, QLabel#brandAttribution { background: transparent; border: 0; }
QGraphicsView { border: 1px solid transparent; }
QGraphicsView[dropActive="true"] { border: 1px dashed #A9B0BA; }
QPushButton[segment] {
    color: #A9B0BA; background: #22252A; border: 1px solid #3A4048;
    padding: 4px 14px; min-height: 18px;
}
QPushButton[segment="left"] { border-radius: 0; border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton[segment="right"] { border-radius: 0; border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-left: 0; }
QPushButton[segment]:hover { color: #F2F3F5; background: #2B2F35; }
QPushButton[segment]:checked { color: #202328; background: #DDE1E6; border-color: #DDE1E6; font-weight: 600; }
QPushButton[segment]:disabled { color: #5D646D; background: #1F2125; border-color: #2E3339; }
QPushButton[segment]:focus { border: 1px solid #F2F3F5; padding: 4px 14px; }
QToolButton#collapsibleHeader {
    color: #A9B0BA; background: transparent; border: 1px solid transparent;
    border-radius: 5px; padding: 4px 4px; font-weight: 600;
}
QToolButton#collapsibleHeader:hover { color: #F2F3F5; background: #22252A; }
QToolButton#collapsibleHeader:focus { border: 1px solid #A9B0BA; }
QPlainTextEdit {
    background: #22252A; color: #F2F3F5; border: 1px solid #3A4048;
    border-radius: 6px; padding: 4px; selection-background-color: #39414B;
}
QPlainTextEdit#collapsibleContent { color: #A9B0BA; }
"""

VIEW_BACKGROUND = QColor("#22252A")
VIEW_DROP_BACKGROUND = QColor("#272B31")
VIEW_TEXT = QColor("#A9B0BA")
