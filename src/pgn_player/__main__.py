"""Keep headless packaged startup failures in a report, never a dialog."""
import json
from pathlib import Path
import sys
import traceback

if __name__ == "__main__":
    try:
        from pgn_player.app import main
        raise SystemExit(main())
    except Exception:
        if "--smoke-test" in sys.argv and "--smoke-output" in sys.argv:
            target = Path(sys.argv[sys.argv.index("--smoke-output") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"ok": False, "error": traceback.format_exc()}, indent=2), encoding="utf-8")
            raise SystemExit(1)
        raise
