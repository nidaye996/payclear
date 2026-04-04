"""
工人管理路由
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

_audit = logging.getLogger("audit")

from database import get_db
from models import Worker, WorkerBankInfo, User, DeletedWorkerArchive, Team
from schemas import WorkerOut, WorkerDetailOut, WorkerBankInfoOut, PaginatedResponse
from routers.auth import get_current_user, require_admin, require_admin_or_operator


class WorkerUpdateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    bank_card: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    routing_number: Optional[str] = None


router = APIRouter(prefix="/workers", tags=["工人管理"])



@router.get("", response_model=PaginatedResponse)
def list_workers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索姓名或身份证"),
    team_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="状态筛选: pending/confirmed"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取工人列表（支持搜索和分页）"""
    query = db.query(Worker)

    # 按队伍过滤
    if team_id:
        if current_user.role not in ("admin", "operator") and current_user.team_id != team_id:
            raise HTTPException(status_code=403, detail="无权访问此队伍")
        query = query.join(WorkerBankInfo).filter(WorkerBankInfo.team_id == team_id).distinct()
    elif current_user.role not in ("admin", "operator"):
        # team_leader 只能看自己队伍的工人
        if current_user.team_id:
            query = query.join(WorkerBankInfo).filter(
                WorkerBankInfo.team_id == current_user.team_id
            ).distinct()

    # 按状态筛选（基于当前有效的 bank_info 的 status）
    if status in ('pending', 'confirmed'):
        # 如果还没 join WorkerBankInfo，先 join
        # 用 exists 子查询来筛选
        from sqlalchemy import exists
        query = query.filter(
            exists().where(
                (WorkerBankInfo.worker_id == Worker.id) &
                (WorkerBankInfo.valid_to == None) &
                (WorkerBankInfo.status == status)
            )
        )

    # 搜索
    if search:
        query = query.filter(
            (Worker.name.contains(search)) | (Worker.id_card.contains(search))
        )

    total = query.count()
    workers = query.offset((page - 1) * page_size).limit(page_size).all()

    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[WorkerDetailOut.model_validate(w) for w in workers]
    )


@router.get("/{worker_id}", response_model=WorkerDetailOut)
def get_worker(
    worker_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取工人详情（含银行信息历史）"""
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="工人不存在")

    # 权限检查
    if current_user.role not in ("admin", "operator") and current_user.team_id:
        team_bank_info = db.query(WorkerBankInfo).filter(
            WorkerBankInfo.worker_id == worker_id,
            WorkerBankInfo.team_id == current_user.team_id
        ).first()
        if not team_bank_info:
            raise HTTPException(status_code=403, detail="无权访问此工人信息")

    return WorkerDetailOut.model_validate(worker)


@router.put("/{worker_id}")
def update_worker(
    worker_id: int,
    data: WorkerUpdateRequest,
    current_user: User = Depends(require_admin_or_operator),
    db: Session = Depends(get_db)
):
    """修改工人信息（仅管理员）"""
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="工人不存在")

    if data.name:
        worker.name = data.name
    if data.phone is not None:
        worker.phone = data.phone

    # 更新当前有效的银行信息
    bank_info = db.query(WorkerBankInfo).filter(
        WorkerBankInfo.worker_id == worker_id,
        WorkerBankInfo.valid_to == None
    ).first()

    if bank_info:
        if data.bank_card is not None:
            bank_info.bank_card = data.bank_card
        if data.bank_name is not None:
            bank_info.bank_name = data.bank_name
        if data.bank_branch is not None:
            bank_info.bank_branch = data.bank_branch
        if data.routing_number is not None:
            bank_info.routing_number = data.routing_number

    db.commit()
    return {"message": "修改成功"}



class DeleteWorkerRequest(BaseModel):
    reason: Optional[str] = None


@router.delete("/{worker_id}")
def delete_worker(
    worker_id: int,
    data: DeleteWorkerRequest,
    current_user: User = Depends(require_admin_or_operator),
    db: Session = Depends(get_db)
):
    """硬删除工人（仅管理员），删前备份到归档表"""
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="工人不存在")

    # 获取当前有效银行信息
    bank_info = db.query(WorkerBankInfo).filter(
        WorkerBankInfo.worker_id == worker_id,
        WorkerBankInfo.valid_to == None
    ).first()

    # 获取队伍名称
    team_name = None
    if bank_info and bank_info.team_id:
        team = db.query(Team).filter(Team.id == bank_info.team_id).first()
        team_name = team.name if team else None

    # 备份到归档表
    archive = DeletedWorkerArchive(
        original_worker_id=worker.id,
        name=worker.name,
        id_card=worker.id_card,
        phone=worker.phone,
        bank_card=bank_info.bank_card if bank_info else None,
        bank_name=bank_info.bank_name if bank_info else None,
        bank_branch=bank_info.bank_branch if bank_info else None,
        routing_number=bank_info.routing_number if bank_info else None,
        team_id=bank_info.team_id if bank_info else None,
        team_name=team_name,
        status=bank_info.status if bank_info else None,
        deleted_by=current_user.id,
        delete_reason=data.reason,
    )
    db.add(archive)

    # 硬删除（关联的 bank_infos 会级联删除）
    db.delete(worker)
    db.commit()

    _audit.info("WORKER_DELETE name=%s id_card=%s by=%s reason=%s",
                worker.name, worker.id_card, current_user.username, data.reason or "")
    return {"message": f"工人 {worker.name} 已删除并归档"}


@router.get("/{worker_id}/bank-history", response_model=List[WorkerBankInfoOut])
def get_worker_bank_history(
    worker_id: int,
    team_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取工人银行信息历史"""
    query = db.query(WorkerBankInfo).filter(WorkerBankInfo.worker_id == worker_id)

    if team_id:
        query = query.filter(WorkerBankInfo.team_id == team_id)
    elif current_user.role != "admin" and current_user.team_id:
        query = query.filter(WorkerBankInfo.team_id == current_user.team_id)

    records = query.order_by(WorkerBankInfo.valid_from.desc()).all()
    return [WorkerBankInfoOut.model_validate(r) for r in records]
