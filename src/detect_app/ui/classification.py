from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from detect_app.persistence.repository import AnalysisRepository, ClassificationError


def pcba_label(pcba: dict, with_client: bool = True) -> str:
    if not with_client:
        return pcba["name"]
    return f"{pcba['name']} · {pcba['client_name'] or 'sans client'}"


def classification_path(log: dict, include_job: bool = True) -> str:
    """Client / PCBA / Job with explicit wording for each missing level."""
    if not log.get("pcba_id"):
        parts = ["Sans PCBA"]
    else:
        parts = [log.get("client_name") or "Sans client", log.get("pcba_name") or "PCBA"]
    if include_job:
        parts.append(f"Job {str(log['id'])[:8]}")
    return " / ".join(parts)


def create_client_interactively(parent: QWidget, repository: AnalysisRepository) -> dict | None:
    name, accepted = QInputDialog.getText(parent, "Nouveau client", "Nom du client :")
    if not accepted:
        return None
    try:
        return repository.create_client(name)
    except ClassificationError as exc:
        QMessageBox.warning(parent, "Client non créé", str(exc))
        return None


class PcbaDialog(QDialog):
    def __init__(self, repository: AnalysisRepository, client_id: str | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Nouveau PCBA")
        self._repository = repository
        self.created: dict | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Référence ou nom du PCBA")
        self.client = QComboBox()
        self.client.addItem("Aucun client", None)
        for client in repository.list_clients():
            self.client.addItem(client["name"], client["id"])
        index = self.client.findData(client_id) if client_id else 0
        self.client.setCurrentIndex(max(0, index))
        form.addRow("Nom", self.name)
        form.addRow("Client (facultatif)", self.client)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Créer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        try:
            self.created = self._repository.create_pcba(self.name.text(), self.client.currentData())
        except ClassificationError as exc:
            QMessageBox.warning(self, "PCBA non créé", str(exc))
            return
        super().accept()


def create_pcba_interactively(
    parent: QWidget, repository: AnalysisRepository, client_id: str | None
) -> dict | None:
    dialog = PcbaDialog(repository, client_id, parent)
    return dialog.created if dialog.exec() == QDialog.DialogCode.Accepted else None


class ClassificationDialog(QDialog):
    """Edit the PCBA of one job; changing the PCBA's client is explicit and confirmed."""

    def __init__(self, repository: AnalysisRepository, job: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Modifier le classement")
        self.setMinimumWidth(460)
        self._repository = repository
        self._job = job
        self.changed = False
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel(
            f"Analyse {str(job['id'])[:8]} · le dossier, l’image et les objets du job ne sont pas modifiés."
        )
        intro.setProperty("secondary", True)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        pcba_row = QHBoxLayout()
        self.pcba = QComboBox()
        self.pcba.setMinimumWidth(220)
        self.new_pcba = QPushButton("Nouveau PCBA…")
        self.new_pcba.clicked.connect(self._create_pcba)
        pcba_row.addWidget(self.pcba, 1)
        pcba_row.addWidget(self.new_pcba)
        form.addRow("PCBA du job", pcba_row)
        client_row = QHBoxLayout()
        self.client = QComboBox()
        self.new_client = QPushButton("Nouveau client…")
        self.new_client.clicked.connect(self._create_client)
        client_row.addWidget(self.client, 1)
        client_row.addWidget(self.new_client)
        form.addRow("Client du PCBA", client_row)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setProperty("secondary", True)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        delete_row = QHBoxLayout()
        self.delete_pcba = QPushButton("Supprimer ce PCBA…")
        self.delete_pcba.clicked.connect(self._delete_pcba)
        self.delete_client = QPushButton("Supprimer ce client…")
        self.delete_client.clicked.connect(self._delete_client)
        delete_row.addWidget(self.delete_pcba)
        delete_row.addWidget(self.delete_client)
        delete_row.addStretch()
        layout.addLayout(delete_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.pcba.currentIndexChanged.connect(lambda _index: self._sync_client())
        self.client.currentIndexChanged.connect(self._update_note)
        self._reload(job.get("pcba_id"))

    def _reload(self, pcba_id: str | None, client_id: str | None = None, keep_client: bool = False) -> None:
        self.pcba.blockSignals(True)
        self.pcba.clear()
        self.pcba.addItem("Aucun PCBA (retirer le lien)", None)
        for pcba in self._repository.list_pcbas():
            self.pcba.addItem(pcba_label(pcba), pcba["id"])
        self.pcba.setCurrentIndex(max(0, self.pcba.findData(pcba_id)) if pcba_id else 0)
        self.pcba.blockSignals(False)
        self.client.blockSignals(True)
        self.client.clear()
        self.client.addItem("Aucun client", None)
        for client in self._repository.list_clients():
            self.client.addItem(client["name"], client["id"])
        self.client.blockSignals(False)
        self._sync_client(client_id, keep_client)

    def _stored_client(self) -> str | None:
        pcba_id = self.pcba.currentData()
        pcba = self._repository.get_pcba(pcba_id) if pcba_id else None
        return pcba["client_id"] if pcba else None

    def _sync_client(self, client_id: str | None = None, keep_client: bool = False) -> None:
        has_pcba = self.pcba.currentData() is not None
        target = client_id if keep_client else self._stored_client()
        self.client.blockSignals(True)
        self.client.setCurrentIndex(max(0, self.client.findData(target)) if target else 0)
        self.client.blockSignals(False)
        self.client.setEnabled(has_pcba)
        self.new_client.setEnabled(has_pcba)
        self.delete_pcba.setEnabled(has_pcba)
        self._update_note()

    def _update_note(self, *_args) -> None:
        pcba_id = self.pcba.currentData()
        self.delete_client.setEnabled(self.client.currentData() is not None)
        if pcba_id is None:
            self.note.setText("Le job restera sans PCBA : il apparaîtra comme « Sans PCBA » dans l’historique.")
            return
        count = self._repository.count_jobs_for_pcba(pcba_id)
        if self.client.currentData() != self._stored_client():
            self.note.setText(
                f"Attention : le client appartient au PCBA. Ce changement reclassera toutes les analyses "
                f"rattachées à ce PCBA ({count} actuellement), pas seulement ce job."
            )
        else:
            self.note.setText(f"Ce PCBA regroupe actuellement {count} analyse(s).")

    def _create_pcba(self) -> None:
        created = create_pcba_interactively(self, self._repository, None)
        if created:
            self.changed = True
            self._reload(created["id"])

    def _create_client(self) -> None:
        created = create_client_interactively(self, self._repository)
        if created:
            self.changed = True
            self._reload(self.pcba.currentData(), created["id"], keep_client=True)

    def _delete_pcba(self) -> None:
        pcba_id = self.pcba.currentData()
        if pcba_id is None:
            return
        if QMessageBox.question(
            self, "Supprimer le PCBA", "Supprimer ce PCBA ? Les analyses ne sont jamais supprimées."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_pcba(pcba_id)
        except ClassificationError as exc:
            QMessageBox.warning(self, "Suppression impossible", str(exc))
            return
        self.changed = True
        self._reload(None)

    def _delete_client(self) -> None:
        client_id = self.client.currentData()
        if client_id is None:
            return
        if QMessageBox.question(
            self, "Supprimer le client", "Supprimer ce client ? Ses PCBA et analyses ne sont jamais supprimés."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_client(client_id)
        except ClassificationError as exc:
            QMessageBox.warning(self, "Suppression impossible", str(exc))
            return
        self.changed = True
        self._reload(self.pcba.currentData())

    def accept(self) -> None:
        pcba_id = self.pcba.currentData()
        client_id = self.client.currentData()
        try:
            if pcba_id is not None and client_id != self._stored_client():
                pcba = self._repository.get_pcba(pcba_id)
                count = self._repository.count_jobs_for_pcba(pcba_id)
                if self._job.get("pcba_id") != pcba_id:
                    count += 1
                answer = QMessageBox.question(
                    self,
                    "Changer le client du PCBA",
                    f"Le PCBA « {pcba['name']} » sera rattaché à "
                    f"« {self.client.currentText()} ». Cela reclasse ses {count} analyse(s), "
                    "pas uniquement l’analyse sélectionnée. Continuer ?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self._repository.set_pcba_client(pcba_id, client_id)
                self.changed = True
            if pcba_id != self._job.get("pcba_id"):
                self._repository.set_job_pcba(self._job["id"], pcba_id)
                self.changed = True
        except ClassificationError as exc:
            QMessageBox.warning(self, "Classement non modifié", str(exc))
            return
        super().accept()
