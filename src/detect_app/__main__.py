from __future__ import annotations

import sys

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from detect_app.config import AppPaths
from detect_app.persistence.database import create_session_factory
from detect_app.persistence.repository import AnalysisRepository
from detect_app.services.analysis import AnalysisService
from detect_app.services.models import ModelRegistry
from detect_app.ui.window import MainWindow


def main() -> int:
    paths = AppPaths.create()
    application = QApplication(sys.argv)
    application.setApplicationName("detect_app")
    windows_icon = paths.icon.parent / "nuts-app.ico"
    app_icon = windows_icon if sys.platform == "win32" and windows_icon.is_file() else paths.icon
    if app_icon.is_file():
        application.setWindowIcon(QIcon(str(app_icon)))
    instance_lock = QLockFile(str(paths.data_dir / "detect_app.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        message = (
            "detect_app est déjà ouvert. Utilisez la fenêtre existante."
            if instance_lock.error() == QLockFile.LockError.LockFailedError
            else "Impossible de verrouiller les données locales. Vérifiez les droits d'accès."
        )
        QMessageBox.warning(None, "Démarrage impossible", message)
        return 1
    try:
        session_factory = create_session_factory(paths.database)
        repository = AnalysisRepository(session_factory)
        repository.recover_interrupted_jobs()
        model_registry = ModelRegistry(paths.models, paths.configuration)
        startup_error = None
        try:
            model_registry.ensure_bundled_model(paths.bundled_model)
        except Exception as exc:
            startup_error = str(exc)
        service = AnalysisService(repository, model_registry, paths.images)
        window = MainWindow(paths, repository, model_registry, service, startup_error)
        window.show()
        return application.exec()
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
