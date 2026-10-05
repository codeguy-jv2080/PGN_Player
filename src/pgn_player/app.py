"""Application entry point, with an entirely offscreen packaged smoke test."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

from pgn_player import __version__


def _edition_mutex(edition: str):
    """Advertise a running installed edition to the installer; never close it."""
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, f"PGNPlayer.{edition.capitalize()}")
    if not handle:
        raise OSError(ctypes.get_last_error(), "Cannot register application running state")
    return handle


def _smoke_test(app, output: Path) -> int:
    """Run the real window and loader with temporary data, never a user profile."""
    from pgn_player.paths import resolve_paths
    from pgn_player.storage import SettingsStore
    from pgn_player.ui.main_window import MainWindow

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {"version": __version__, "ok": False, "offscreen": True}
    window = None
    store = None
    with tempfile.TemporaryDirectory(prefix="pgn-player-smoke-") as temporary:
        try:
            scratch = Path(temporary)
            paths = resolve_paths(data_override=scratch / "profile")
            result["edition"] = paths.edition
            store = SettingsStore(paths.db_path)
            fixture = scratch / "smoke.pgn"
            fixture.write_text(
                '[Event "Playback sample"]\n[White "White"]\n[Black "Black"]\n'
                '[Result "*"]\n\n1. e4 {Center control.} e5 (1... c5 2. Nf3) '
                '2. Nf3 Nc6 3. Bb5 a6 *\n\n'
                '[Event "Second sample"]\n[White "White"]\n[Black "Black"]\n'
                '[Result "*"]\n\n1. d4 d5 2. c4 e6 *\n', encoding="utf-8"
            )
            window = MainWindow(store, paths)
            window.show()
            window.open_file(fixture)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                app.processEvents()
                if window.player.collection is not None and not window._loading:
                    break
                time.sleep(0.01)
            assert window.player.collection is not None, "PGN did not finish loading"
            assert len(window.player.collection) == 2, "Expected two indexed games"
            assert window.player.next(), "First move navigation failed"
            assert window.player.board.piece_at(28) is not None, "Pawn missing from e4"
            window.player.previous()
            assert window.player.next_game(), "Next game navigation failed"
            window.player.previous_game()
            assert window.notation_panel.isVisible(), "Notation should start visible"
            board_width = window.board.width()
            window.notation_button.click()
            app.processEvents()
            assert window.notation_panel.isHidden(), "Notation did not hide"
            assert window.board.width() > board_width, "Board did not reclaim sidebar space"
            assert window.board.coordinates_visible, "Coordinates should start visible"
            window.coordinates_action.trigger()
            assert not window.board.coordinates_visible, "Coordinates did not hide"
            # Recreate the window so this proves persistent SQLite reuse rather
            # than the same-window optimization that shares a loaded index.
            window.close()
            app.processEvents()
            window.deleteLater()
            window = MainWindow(store, paths)
            window.show()
            assert window.notation_panel.isHidden(), "Notation visibility was not remembered"
            assert not window.board.coordinates_visible, "Coordinate visibility was not remembered"
            window.coordinates_action.trigger()
            assert window.board.coordinates_visible, "View menu did not restore coordinates"
            window.notation_action.trigger()
            app.processEvents()
            assert window.notation_panel.isVisible(), "View menu did not restore notation"
            assert store.get_settings()["notation_visible"] is True
            window.open_file(fixture, restore=False)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                app.processEvents()
                if not window._loading and window._worker is None:
                    break
                time.sleep(0.01)
            assert not window._loading, "Saved index did not finish loading"
            assert window.player.collection.from_cache, "Second open did not reuse the saved index"
            # Trigger the same rendering path as user navigation.
            if hasattr(window, "refresh"):
                window.refresh()
            elif hasattr(window, "_refresh"):
                window._refresh()
            app.processEvents()
            light = output.with_name(output.stem + "-light.png")
            dark = output.with_name(output.stem + "-dark.png")
            if window.theme != "light":
                window.toggle_theme()
            app.processEvents()
            assert window.grab().save(str(light)), "Cannot render light UI"
            window.toggle_theme()
            app.processEvents()
            assert window.theme == "dark", "Theme toggle failed"
            assert window.grab().save(str(dark)), "Cannot render dark UI"
            window.close()
            app.processEvents()
            result.update(ok=True, games=2, checks=["launch", "open", "board", "navigation", "notation_visibility", "board_coordinates", "saved_index", "themes", "close"])
        except Exception:
            result["error"] = traceback.format_exc()
        finally:
            if window is not None:
                window.close()
                deadline = time.monotonic() + 10
                while getattr(window, "_loading", False) and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
            if store is not None:
                store.close()
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PGN Player — local chess playback and study")
    parser.add_argument("pgn", nargs="?", help="PGN file to open")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.smoke_test:
        if args.smoke_output is None:
            parser.error("--smoke-test requires --smoke-output")
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    from pgn_player.paths import resolve_paths
    from pgn_player.storage import SettingsStore
    from pgn_player.ui.main_window import MainWindow

    app = QApplication([sys.argv[0]])
    app.setApplicationName("PGN Player")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PGN Player")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "app.svg")))
    if args.smoke_test:
        # Qt's offscreen plugin on Windows does not enumerate system fonts.
        # Register installed fonts for QA only; no fonts are copied or shipped.
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for font_name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
            font_path = font_dir / font_name
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Segoe UI", 10))
        return _smoke_test(app, args.smoke_output)
    store = None
    handle = None
    try:
        paths = resolve_paths()
        handle = _edition_mutex(paths.edition)
        store = SettingsStore(paths.db_path)
        window = MainWindow(store, paths)
        window.show()
        if args.pgn:
            window.open_file(Path(args.pgn))
        else:
            window.reopen_last_file()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "PGN Player could not start", str(exc))
        return 1
    finally:
        if store is not None:
            store.close()
        if handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
