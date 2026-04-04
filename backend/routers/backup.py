"""
数据备份与恢复路由
GET  /api/backup/download  - 下载备份 zip（所有角色）
POST /api/backup/restore   - 上传恢复备份（所有角色）
"""
import os
import json
import shutil
import zipfile
import tempfile
import logging
from datetime import datetime

_audit = logging.getLogger("audit")

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db, engine, DATA_DIR
from models import User
from routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["备份恢复"])

DB_PATH = os.path.join(DATA_DIR, "salary.db")


@router.get("/download")
def backup_download(
    current_user: User = Depends(get_current_user),
):
    """
    下载数据备份（zip 格式，含 salary.db + 备份信息）
    """
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="数据库文件不存在")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"payclear_backup_{timestamp}.zip"

    tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_zip.close()

    try:
        # 先用 SQLite 的 backup API 把数据库复制一份，避免文件锁问题
        import sqlite3
        tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp_db.close()

        src_conn = sqlite3.connect(DB_PATH)
        dst_conn = sqlite3.connect(tmp_db.name)
        src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()

        # 打包成 zip
        backup_info = {
            "system": "薪核通 PayClear",
            "backup_time": datetime.now().isoformat(),
            "created_by": current_user.username,
            "version": "1.0",
        }

        with zipfile.ZipFile(tmp_zip.name, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db.name, "salary.db")
            zf.writestr("backup_info.json", json.dumps(backup_info, ensure_ascii=False, indent=2))

        os.unlink(tmp_db.name)

        _audit.info("BACKUP_DOWNLOAD by=%s file=%s", current_user.username, zip_filename)
        return FileResponse(
            tmp_zip.name,
            media_type="application/zip",
            filename=zip_filename,
            headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
            background=None,
        )

    except Exception as e:
        try:
            os.unlink(tmp_zip.name)
        except Exception:
            pass
        logger.error(f"备份失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"备份失败: {str(e)}")


@router.post("/restore")
async def backup_restore(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    恢复数据备份（上传 zip 文件，替换当前数据库）
    恢复后立即生效，无需重启服务。
    """
    filename = file.filename or ""
    if not filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 格式的备份文件")

    tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        tmp_zip.write(await file.read())
        tmp_zip.close()

        # 验证 zip 内容
        with zipfile.ZipFile(tmp_zip.name, "r") as zf:
            names = zf.namelist()
            if "salary.db" not in names:
                raise HTTPException(status_code=400, detail="备份文件无效：缺少 salary.db")

            # 读取备份信息
            backup_info = {}
            if "backup_info.json" in names:
                try:
                    backup_info = json.loads(zf.read("backup_info.json"))
                except Exception:
                    pass

            # 提取 salary.db 到临时文件
            tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            tmp_db.write(zf.read("salary.db"))
            tmp_db.close()

        # 验证提取出的文件是合法的 SQLite 数据库
        import sqlite3
        try:
            conn = sqlite3.connect(tmp_db.name)
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            conn.close()
        except Exception:
            raise HTTPException(status_code=400, detail="备份文件损坏，无法解析数据库")

        # 关闭所有 SQLAlchemy 连接，再替换文件
        engine.dispose()

        # 保留一份当前数据库作为临时快照（防止意外）
        backup_current = DB_PATH + ".before_restore"
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, backup_current)

        # 替换数据库
        shutil.move(tmp_db.name, DB_PATH)

        _audit.info("BACKUP_RESTORE by=%s backup_time=%s", current_user.username, backup_info.get("backup_time", "unknown"))

        return {
            "message": "恢复成功，数据已更新",
            "backup_time": backup_info.get("backup_time", "未知"),
            "created_by": backup_info.get("created_by", "未知"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"恢复失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"恢复失败: {str(e)}")
    finally:
        try:
            os.unlink(tmp_zip.name)
        except Exception:
            pass
