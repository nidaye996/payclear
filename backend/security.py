"""
安全相关的轻量工具函数。
"""
import os
import re
import secrets
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile


DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_BACKUP_UPLOAD_BYTES = 512 * 1024 * 1024
MIN_PASSWORD_LENGTH = 10


def require_strong_password(password: str) -> None:
    """校验账号密码强度。"""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if password.lower() in {"admin123", "password", "123456", "12345678", "qwerty"}:
        raise HTTPException(status_code=400, detail="密码过于简单，请更换")


def generate_secret_key() -> str:
    """生成适合 SECRET_KEY 使用的随机密钥。"""
    return secrets.token_urlsafe(48)


def safe_display_filename(filename: str | None) -> str:
    """清理原始文件名，仅用于展示和记录，不用于磁盘路径拼接。"""
    name = Path(filename or "upload").name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    return name[:180] or "upload"


def safe_storage_filename(original_filename: str | None, prefix: str, allowed_extensions: Iterable[str]) -> str:
    """生成安全的磁盘文件名，避免使用用户上传的原始路径。"""
    display_name = safe_display_filename(original_filename)
    ext = Path(display_name).suffix.lower()
    allowed = {item.lower() for item in allowed_extensions}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"文件格式不支持，只允许: {', '.join(sorted(allowed))}")
    return f"{prefix}_{secrets.token_hex(8)}{ext}"


async def read_upload_file(file: UploadFile, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> bytes:
    """按块读取上传内容，并限制最大大小。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件过大，最大允许 {max_bytes // 1024 // 1024}MB")
        chunks.append(chunk)
    return b"".join(chunks)


def ensure_path_under(base_dir: str, target_path: str) -> None:
    """确认目标路径仍在指定目录下。"""
    base = os.path.realpath(base_dir)
    target = os.path.realpath(target_path)
    if os.path.commonpath([base, target]) != base:
        raise HTTPException(status_code=400, detail="文件路径非法")
