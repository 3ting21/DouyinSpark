from __future__ import annotations

import ctypes
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DouyinSpark"
DB_PATH = APP_DIR / "data.db"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        return b"PLAIN1" + data
    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    source = _Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    if not crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise OSError("Windows DPAPI encryption failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        return data[6:] if data.startswith(b"PLAIN1") else data
    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    source = _Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise OSError("Windows DPAPI decryption failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


class EncryptedStore:
    """A small encrypted record store backed by ordinary SQLite."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS records (record_key TEXT PRIMARY KEY, payload BLOB NOT NULL, updated_at TEXT NOT NULL)"
        )
        self.connection.commit()

    def load(self, record_key: str, default: Any) -> Any:
        row = self.connection.execute(
            "SELECT payload FROM records WHERE record_key = ?", (record_key,)
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(_dpapi_unprotect(row[0]).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("本地数据无法解密，可能已损坏或由其他 Windows 用户创建") from exc

    def save(self, record_key: str, value: Any) -> None:
        payload = _dpapi_protect(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        self.connection.execute(
            "INSERT INTO records(record_key, payload, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(record_key) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
            (record_key, payload, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def export_backup(store: EncryptedStore, destination: Path) -> None:
    """Export encrypted database bytes; the backup remains encrypted."""
    destination.write_bytes(store.db_path.read_bytes())


def import_backup(store: EncryptedStore, source: Path) -> None:
    """Import an encrypted database created by the same Windows user."""
    store.close()
    store.db_path.write_bytes(source.read_bytes())
