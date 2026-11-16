from __future__ import annotations

import json
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from .storage import APP_DIR, EncryptedStore
from .ui import MainWindow, enable_dpi_awareness


def _selftest() -> int:
    """Verify the packaged Playwright driver + Edge channel without opening the GUI."""
    from .browser import run_selftest

    report = run_selftest()
    report["checked_at"] = datetime.now().isoformat(timespec="seconds")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    Path(APP_DIR / "selftest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if report.get("ok") else 1


def _open_check(nickname: str) -> int:
    """Open one thread and confirm its header without sending anything."""
    from .browser import ChatSession, ChatUnavailable

    report: dict[str, object] = {"ok": False, "nickname": nickname}
    session = ChatSession()
    try:
        report["page"] = session.open_chat()
        report["route"] = session.open_conversation(nickname)
        report["editor"] = session._wait_editor(12.0) is not None
        report["dump"] = session._dump_debug("open-check")
        report["ok"] = bool(report.get("editor"))
    except ChatUnavailable as exc:
        report["detail"] = str(exc)
    except Exception as exc:  # pragma: no cover - diagnostics only
        report["detail"] = f"{type(exc).__name__}: {exc}"
    finally:
        session.stop()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    Path(APP_DIR / "opencheck.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


def main() -> int:
    enable_dpi_awareness()
    if "--selftest" in sys.argv:
        return _selftest()
    if "--open-check" in sys.argv:
        index = sys.argv.index("--open-check")
        nickname = sys.argv[index + 1] if index + 1 < len(sys.argv) else ""
        if not nickname:
            print("用法：--open-check <好友昵称>")
            return 2
        return _open_check(nickname)
    try:
        store = EncryptedStore()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("启动失败", f"无法打开本地数据：{exc}")
        root.destroy()
        return 1
    try:
        window = MainWindow(store)
    except Exception as exc:
        store.close()
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("启动失败", f"无法读取本地数据：{exc}")
        root.destroy()
        return 1
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
