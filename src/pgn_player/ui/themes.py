"""Consistent, accessible light and dark presentation."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    "light": dict(background="#F3F5F8", surface="#FFFFFF", text="#172234", muted="#5C697B", border="#DCE2EA", light_square="#EDF0F4", dark_square="#8094AF"),
    "dark": dict(background="#171C25", surface="#222A36", text="#E7EDF6", muted="#AEBBCD", border="#3B4657", light_square="#B0BCCB", dark_square="#5B708F"),
}


def apply_theme(theme: str) -> None:
    app = QApplication.instance()
    if app is None:
        return
    colors = COLORS[theme]
    palette = QPalette()
    for role, value in ((QPalette.ColorRole.Window, colors["background"]), (QPalette.ColorRole.Base, colors["surface"]),
                        (QPalette.ColorRole.AlternateBase, colors["background"]), (QPalette.ColorRole.WindowText, colors["text"]),
                        (QPalette.ColorRole.Text, colors["text"]), (QPalette.ColorRole.PlaceholderText, colors["muted"]),
                        (QPalette.ColorRole.Button, colors["surface"]),
                        (QPalette.ColorRole.ButtonText, colors["text"]), (QPalette.ColorRole.Highlight, "#007BFF"),
                        (QPalette.ColorRole.HighlightedText, "#FFFFFF"), (QPalette.ColorRole.ToolTipBase, colors["surface"]),
                        (QPalette.ColorRole.ToolTipText, colors["text"]), (QPalette.ColorRole.Link, "#007BFF")):
        palette.setColor(role, QColor(value))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors["muted"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(colors["muted"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ font-family: 'Segoe UI', sans-serif; font-size: 10pt; }}
        QMainWindow, QDialog {{ background: {colors['background']}; }}
        QPushButton, QToolButton {{ border: 1px solid {colors['border']}; border-radius: 5px; padding: 6px 10px; background: {colors['surface']}; }}
        QPushButton:hover, QToolButton:hover {{ border-color: #007BFF; }}
        QPushButton:checked, QToolButton:checked, QPushButton#primary {{ background: #007BFF; color: white; border-color: #007BFF; }}
        QPushButton:disabled {{ color: {colors['muted']}; }}
        QLineEdit, QComboBox, QDoubleSpinBox {{ background: {colors['surface']}; border: 1px solid {colors['border']}; border-radius: 4px; padding: 5px; }}
        QTextBrowser, QTableView {{ border: 1px solid {colors['border']}; border-radius: 5px; background: {colors['surface']}; }}
        QHeaderView::section {{ background: {colors['background']}; color: {colors['muted']}; border: none; border-bottom: 1px solid {colors['border']}; padding: 7px; }}
        QGroupBox {{ border: 1px solid {colors['border']}; border-radius: 5px; margin-top: 10px; padding-top: 9px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
        QSplitter::handle {{ background: {colors['background']}; }}
        QSplitter::handle:hover {{ background: #007BFF; }}
        QLabel#muted {{ color: {colors['muted']}; }}
        QLabel#title {{ font-size: 14pt; font-weight: 600; }}
        QStatusBar {{ color: {colors['muted']}; }}
        QProgressBar {{ border: 1px solid {colors['border']}; border-radius: 3px; text-align: center; }}
        QProgressBar::chunk {{ background: #007BFF; }}
    """)
