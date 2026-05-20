"""
用工协议路由：上传、查询、删除
"""
import os
import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session

from database import get_db
from models import WorkerContract, Worker, User
from routers.auth import get_current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["用工协议"])

CONTRACTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "contracts"
)
os.makedirs(CONTRACTS_DIR, exist_ok=True)


def _save_file(file_content: bytes, filename: str, contract_id: int) -> str:
    sub_dir = os.path.join(CONTRACTS_DIR, str(contract_id))
    os.makedirs(sub_dir, exist_ok=True)
    file_path = os.path.join(sub_dir, filename)
    with open(file_path, "wb") as f:
        f.write(file_content)
    return file_path


def _contract_to_dict(c: WorkerContract, db: Session) -> dict:
    worker = db.query(Worker).filter(Worker.id == c.worker_id).first() if c.worker_id else None
    # 检查同一身份证是否有多份协议
    dup_count = db.query(WorkerContract).filter(
        WorkerContract.id_card == c.id_card,
        WorkerContract.id != c.id,
        WorkerContract.id_card != '',
        WorkerContract.id_card.isnot(None),
    ).count() if c.id_card else 0

    return {
        "id": c.id,
        "name": c.name,
        "id_card": c.id_card,
        "daily_wage": c.daily_wage,
        "original_filename": c.original_filename,
        "template_valid": c.template_valid,
        "ocr_status": c.ocr_status,
        "ocr_error": c.ocr_error,
        "uploaded_at": c.uploaded_at.isoformat() if c.uploaded_at else None,
        "worker_id": c.worker_id,
        "worker_matched": worker is not None,
        "worker_name_in_db": worker.name if worker else None,
        "has_duplicate": dup_count > 0,
    }


@router.post("/upload")
async def upload_contracts(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量上传用工协议PDF（管理员）"""
    from services.ocr import parse_contract_pdf
    import tempfile, shutil

    results = []
    for file in files:
        if not (file.filename or '').lower().endswith('.pdf'):
            results.append({"filename": file.filename, "error": "只支持PDF格式"})
            continue

        content = await file.read()

        # 先存到临时文件做OCR
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            ocr_result = parse_contract_pdf(tmp_path)
        except Exception as e:
            ocr_result = {'name': '', 'id_card': '', 'daily_wage': None,
                         'template_valid': False, 'error': str(e)}
        finally:
            os.unlink(tmp_path)

        # 查找匹配工人
        worker_id = None
        if ocr_result.get('id_card'):
            worker = db.query(Worker).filter(
                Worker.id_card == ocr_result['id_card']
            ).first()
            if worker:
                worker_id = worker.id

        # 创建协议记录
        contract = WorkerContract(
            worker_id=worker_id,
            id_card=ocr_result.get('id_card', ''),
            name=ocr_result.get('name', ''),
            daily_wage=ocr_result.get('daily_wage'),
            original_filename=file.filename,
            template_valid=ocr_result.get('template_valid', False),
            ocr_status='failed' if ocr_result.get('error') else 'done',
            ocr_error=ocr_result.get('error'),
            uploaded_by=current_user.id,
        )
        db.add(contract)
        db.flush()  # 获取ID

        # 保存PDF文件
        file_path = _save_file(content, file.filename, contract.id)
        contract.file_path = file_path
        db.commit()
        db.refresh(contract)

        results.append(_contract_to_dict(contract, db))

    return {"uploaded": len(results), "results": results}


@router.get("")
def list_contracts(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="all/matched/unmatched/invalid"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用工协议列表"""
    query = db.query(WorkerContract)

    if search:
        query = query.filter(
            (WorkerContract.name.contains(search)) |
            (WorkerContract.id_card.contains(search))
        )
    if status == 'matched':
        query = query.filter(WorkerContract.worker_id.isnot(None))
    elif status == 'unmatched':
        query = query.filter(WorkerContract.worker_id.is_(None))
    elif status == 'invalid':
        query = query.filter(WorkerContract.template_valid == False)  # noqa: E712

    contracts = query.order_by(WorkerContract.uploaded_at.desc()).all()
    return [_contract_to_dict(c, db) for c in contracts]


@router.get("/stats")
def contract_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """协议统计：总数、已匹配、模板不合规、未匹配工人数"""
    total = db.query(WorkerContract).count()
    matched = db.query(WorkerContract).filter(WorkerContract.worker_id.isnot(None)).count()
    invalid_template = db.query(WorkerContract).filter(
        WorkerContract.template_valid == False  # noqa: E712
    ).count()
    # 工人库中没有协议的工人数
    from sqlalchemy import text
    no_contract = db.execute(text(
        "SELECT COUNT(*) FROM workers w WHERE NOT EXISTS "
        "(SELECT 1 FROM worker_contracts c WHERE c.worker_id = w.id)"
    )).scalar()
    return {
        "total": total,
        "matched": matched,
        "unmatched": total - matched,
        "invalid_template": invalid_template,
        "workers_without_contract": no_contract,
    }


@router.delete("/{contract_id}")
def delete_contract(
    contract_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """删除用工协议（管理员）"""
    import shutil
    contract = db.query(WorkerContract).filter(WorkerContract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="协议不存在")

    # 删除磁盘文件目录
    sub_dir = os.path.join(CONTRACTS_DIR, str(contract_id))
    if os.path.exists(sub_dir):
        shutil.rmtree(sub_dir)

    db.delete(contract)
    db.commit()
    return {"message": "协议已删除"}


@router.get("/by-worker/{id_card}")
def get_contract_by_id_card(
    id_card: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按身份证号查询协议"""
    contracts = db.query(WorkerContract).filter(
        WorkerContract.id_card == id_card
    ).all()
    return [_contract_to_dict(c, db) for c in contracts]
