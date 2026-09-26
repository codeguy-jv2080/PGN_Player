"""Small local playback preference dialog."""
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout


class PreferencesDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences — PGN Player")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.continue_next = QCheckBox("Continue automatically to the next game")
        self.continue_next.setChecked(settings["continue_next"])
        form.addRow(self.continue_next)
        self.loop = QCheckBox("Loop playback")
        self.loop.setChecked(settings["loop"])
        self.loop.setToolTip("Repeat this game, or the entire PGN when continuing to the next game.")
        form.addRow(self.loop)
        self.between = QDoubleSpinBox()
        self.between.setRange(0, 120)
        self.between.setSingleStep(.5)
        self.between.setSuffix(" sec")
        self.between.setValue(settings["between_games_seconds"])
        form.addRow("Pause between games", self.between)
        self.variations = QCheckBox("Include variations in autoplay")
        self.variations.setChecked(settings["include_variations"])
        self.variations.setToolTip("Visit main-line and variation positions in depth-first order.")
        form.addRow(self.variations)
        self.reopen = QCheckBox("Reopen the last PGN when starting")
        self.reopen.setChecked(settings["reopen_last"])
        form.addRow(self.reopen)
        layout.addLayout(form)
        note = QLabel("Changes are saved locally for this edition of PGN Player.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {"continue_next": self.continue_next.isChecked(), "loop": self.loop.isChecked(),
                "between_games_seconds": self.between.value(),
                "include_variations": self.variations.isChecked(), "reopen_last": self.reopen.isChecked()}
