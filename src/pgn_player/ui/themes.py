"""Consistent, accessible light and dark presentation."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    "light": dict(
        background="#F2F2F2", surface="#FFFFFF", controls="#FFFFFF",
        text="#171717", muted="#484848", border="#858585",
        hover="#E6E6E6", focus="#005FCC", disabled_text="#696969",
        disabled_bg="#E6E6E6", disabled_border="#B8B8B8", error="#A51B1B",
        light_square="#F0D9B5", dark_square="#B58863",
    ),
    "dark": dict(
        background="#202020", surface="#2A2A2A", controls="#353535",
        text="#F5F5F5", muted="#D2D2D2", border="#858585",
        hover="#424242", focus="#75BAFF", disabled_text="#A3A3A3",
        disabled_bg="#262626", disabled_border="#555555", error="#FFB7B7",
        light_square="#F0D9B5", dark_square="#B58863",
    ),
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
                        (QPalette.ColorRole.Button, colors["controls"]),
                        (QPalette.ColorRole.ButtonText, colors["text"]), (QPalette.ColorRole.Highlight, "#007BFF"),
                        (QPalette.ColorRole.HighlightedText, "#FFFFFF"), (QPalette.ColorRole.ToolTipBase, colors["surface"]),
                        (QPalette.ColorRole.ToolTipText, colors["text"]), (QPalette.ColorRole.Link, "#007BFF")):
        palette.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_text"]))
    for role in (QPalette.ColorRole.Button, QPalette.ColorRole.Base):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_bg"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ font-family: 'Segoe UI', sans-serif; font-size: 10pt; }}
        QMainWindow, QDialog {{ background: {colors['background']}; }}
        QPushButton, QToolButton {{ border: 1px solid {colors['border']}; border-radius: 5px; padding: 6px 10px; background: {colors['controls']}; color: {colors['text']}; }}
        QPushButton:hover, QToolButton:hover, QComboBox:hover {{ background: {colors['hover']}; border-color: {colors['border']}; }}
        QPushButton:checked, QToolButton:checked, QPushButton#primary {{ background: #007BFF; color: white; border-color: #007BFF; }}
        QLineEdit, QComboBox, QDoubleSpinBox {{ background: {colors['controls']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 4px; padding: 5px; }}
        QPushButton:focus, QPushButton#primary:focus, QToolButton:focus,
        QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {colors['focus']}; }}
        QPushButton:disabled, QPushButton#primary:disabled, QToolButton:disabled,
        QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{
            color: {colors['disabled_text']}; background: {colors['disabled_bg']}; border-color: {colors['disabled_border']};
        }}
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
        QMenuBar, QMenu {{ background: {colors['background']}; color: {colors['text']}; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background: {colors['hover']}; }}
        QToolTip {{ background: {colors['surface']}; color: {colors['text']}; border: 1px solid {colors['border']}; }}
    """)
