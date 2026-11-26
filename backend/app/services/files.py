"""Upload storage and file safety checks."""
from __future__ import annotations

import hashlib
import shutil
import uuid
import zipfile
from pathlib import Path

from ..core.config import Settings
from ..core.utils import safe_ext


class UploadTooLarge(Exception):
    pass


class UploadEmpty(Exception):
    pass


def save_upload(file_obj, settings: Settings, document_id: str, version: int, filename: str) -> tuple[str, int, str]:
    ext = safe_ext(filename)
    target_dir = settings.uploads_dir / document_id
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"v{version}_{uuid.uuid4().hex[:8]}.{ext}"
    temp_path = target_dir / f".{uuid.uuid4().hex}.part"
    hasher = hashlib.sha256()
    size = 0
    try:
        with open(temp_path, "wb") as output:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.upload_max_bytes:
                    raise UploadTooLarge()
                hasher.update(chunk)
                output.write(chunk)
        if size == 0:
            raise UploadEmpty()
        temp_path.replace(final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return str(final_path), size, hasher.hexdigest()


def magic_ok(path: str, fmt: str) -> bool:
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
        if fmt == "PDF":
            return head.startswith(b"%PDF-")
        if fmt == "Word":
            return head.startswith(b"PK\x03\x04")
        if fmt == "Markdown":
            if b"\x00" in head:
                return False
            with open(path, "rb") as handle:
                handle.read(8192).decode("utf-8", errors="replace")
            return True
    except OSError:
        return False
    return False


def validate_docx_zip(path: str, max_uncompressed: int) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 10_000:
                return False
            total = 0
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    return False
                total += info.file_size
                if total > max_uncompressed:
                    return False
            return True
    except (zipfile.BadZipFile, OSError):
        return False


def delete_document_files(settings: Settings, document_id: str) -> None:
    directory = settings.uploads_dir / document_id
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


def delete_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass