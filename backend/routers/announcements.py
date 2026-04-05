"""
公告路由
"""
import logging
from typing import List
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


class AnnouncementOut(BaseModel):
    id: int
    content: str
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
    """获取所有公告（所有登录用户可见）"""
    rows = db.query(Announcement).order_by(Announcement.created_at.desc()).all()
    return [
        AnnouncementOut(
            id=r.id,
            content=r.content,
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
    """发布公告（管理员）"""
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="公告内容不能为空")
    ann = Announcement(content=data.content.strip(), created_by=current_user.id)
    db.add(ann)
    db.commit()
    db.refresh(ann)
    _audit.info("ANNOUNCEMENT_CREATE id=%d by=%s", ann.id, current_user.username)
    return AnnouncementOut(
        id=ann.id,
        content=ann.content,
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
    """删除公告（管理员）"""
    ann = db.query(Announcement).filter(Announcement.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    db.delete(ann)
    db.commit()
    _audit.info("ANNOUNCEMENT_DELETE id=%d by=%s", ann_id, current_user.username)
    return {"message": "已删除"}
