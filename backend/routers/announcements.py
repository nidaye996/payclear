"""
公告路由
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime

from database import get_db
from models import Announcement
from routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/announcements", tags=["公告"])
_audit = logging.getLogger("audit")


class AnnouncementCreate(BaseModel):
    content: str
    type: str = "normal"


class AnnouncementOut(BaseModel):
    id: int
    content: str
    type: str
    created_by: int
    created_at: datetime
    author_name: str

    class Config:
        from_attributes = True


@router.get("", response_model=List[AnnouncementOut])
def list_announcements(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    rows = db.query(Announcement).order_by(Announcement.created_at.desc()).all()
    return [
        AnnouncementOut(
            id=r.id,
            content=r.content,
            type=r.type or "normal",
            created_by=r.created_by,
            created_at=r.created_at,
            author_name=r.author.username if r.author else "未知",
        )
        for r in rows
    ]


@router.post("", response_model=AnnouncementOut)
def create_announcement(
    data: AnnouncementCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="公告内容不能为空")
    if data.type not in ("normal", "fullscreen"):
        raise HTTPException(status_code=400, detail="类型无效")
    ann = Announcement(
        content=data.content.strip(),
        type=data.type,
        created_by=current_user.id,
    )
    db.add(ann)
    db.commit()
    db.refresh(ann)
    _audit.info("ANNOUNCEMENT_CREATE id=%d type=%s by=%s", ann.id, ann.type, current_user.username)
    return AnnouncementOut(
        id=ann.id,
        content=ann.content,
        type=ann.type,
        created_by=ann.created_by,
        created_at=ann.created_at,
        author_name=current_user.username,
    )


@router.delete("/{ann_id}")
def delete_announcement(
    ann_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    ann = db.query(Announcement).filter(Announcement.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    db.delete(ann)
    db.commit()
    _audit.info("ANNOUNCEMENT_DELETE id=%d by=%s", ann_id, current_user.username)
    return {"message": "已删除"}
