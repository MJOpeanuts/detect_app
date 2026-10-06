from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from detect_app.config import AppPaths
from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository
from detect_app.services.analysis import AnalysisService
from detect_app.services.models import ModelRegistry
from detect_app.ui.window import MainWindow


def main() -> int:
    paths = AppPaths.create()
    session_factory = create_session_factory(paths.database)
    repository = AnalysisRepository(session_factory)
    model_registry = ModelRegistry(paths.models, paths.configuration)
    startup_error = None
    try:
        model_registry.ensure_bundled_model(paths.bundled_model)
    except Exception as exc:
        startup_error = str(exc)
    service = AnalysisService(repository, model_registry, paths.images)

    application = QApplication(sys.argv)
    application.setApplicationName("Nuts Vision Desktop")
    if paths.icon.is_file():
        application.setWindowIcon(QIcon(str(paths.icon)))
    window = MainWindow(paths, repository, model_registry, service, startup_error)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
