"""
用工协议路由：上传、查询、编辑、删除、重新识别、替换PDF、批量删除、查缺
"""
import os
import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from sqlalchemy import exists, func
from sqlalchemy.orm import Session

from database import get_db
from models import WorkerBankInfo, WorkerContract, Worker, User
from routers.auth import get_current_user, require_admin
from security import DEFAULT_MAX_UPLOAD_BYTES, read_upload_file, safe_display_filename, safe_storage_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["用工协议"])

CONTRACTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "contracts"
)
os.makedirs(CONTRACTS_DIR, exist_ok=True)


class EditContractRequest(BaseModel):
    name: Optional[str] = None
    id_card: Optional[str] = None


class BulkDeleteRequest(BaseModel):
    ids: List[int]


class CheckMissingRequest(BaseModel):
    filenames: List[str]


ALLOWED_CONTRACT_EXTENSIONS = {'.pdf'}


def _save_file(file_content: bytes, filename: str, contract_id: int) -> str:
    sub_dir = os.path.join(CONTRACTS_DIR, str(contract_id))
    os.makedirs(sub_dir, exist_ok=True)
    storage_name = safe_storage_filename(filename, "contract", ALLOWED_CONTRACT_EXTENSIONS)
    file_path = os.path.join(sub_dir, storage_name)
    with open(file_path, "wb") as f:
        f.write(file_content)
    return file_path


def _visible_contract_query(db: Session, user: User):
    """限制普通队伍负责人只能看到自己队伍已匹配工人的合同。"""
    query = db.query(WorkerContract)
    if user.role in ("admin", "operator"):
        return query
    if not user.team_id:
        return query.filter(False)
    return query.filter(
        WorkerContract.worker_id.isnot(None),
        exists().where(
            (WorkerBankInfo.worker_id == WorkerContract.worker_id) &
            (WorkerBankInfo.team_id == user.team_id)
        )
    )


def _contract_to_dict(c: WorkerContract, db: Session) -> dict:
    worker = db.query(Worker).filter(Worker.id == c.worker_id).first() if c.worker_id else None
    dup_count = db.query(WorkerContract).filter(
        WorkerContract.id_card == c.id_card,
        WorkerContract.id != c.id,
        WorkerContract.id_card != '',
        WorkerContract.id_card.isnot(None),
    ).count() if c.id_card else 0

    missing_kws = []
    if c.missing_keywords:
        missing_kws = [k for k in c.missing_keywords.split(',') if k]

    return {
        "id": c.id,
        "name": c.name,
        "id_card": c.id_card,
        "daily_wage": c.daily_wage,
        "original_filename": c.original_filename,
        "template_valid": c.template_valid,
        "missing_keywords": missing_kws,
        "ocr_status": c.ocr_status,
        "ocr_error": c.ocr_error,
        "uploaded_at": c.uploaded_at.isoformat() if c.uploaded_at else None,
        "worker_id": c.worker_id,
        "worker_matched": worker is not None,
        "worker_name_in_db": worker.name if worker else None,
        "has_duplicate": dup_count > 0,
    }


def _apply_ocr_result(contract: WorkerContract, ocr_result: dict, db: Session):
    """将OCR结果写入合同记录，并尝试重新匹配工人"""
    contract.name = ocr_result.get('name', '') or ''
    contract.id_card = ocr_result.get('id_card', '') or ''
    contract.daily_wage = ocr_result.get('daily_wage')
    contract.template_valid = ocr_result.get('template_valid', False)
    contract.missing_keywords = ','.join(ocr_result.get('missing_keywords', []))
    contract.ocr_status = 'failed' if ocr_result.get('error') else 'done'
    contract.ocr_error = ocr_result.get('error')

    if contract.id_card:
        worker = db.query(Worker).filter(Worker.id_card == contract.id_card).first()
        contract.worker_id = worker.id if worker else None
    else:
        contract.worker_id = None


@router.post("/upload")
async def upload_contracts(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量上传用工协议PDF（管理员）"""
    from services.ocr import parse_contract_pdf
    import tempfile

    results = []
    for file in files:
        if not (file.filename or '').lower().endswith('.pdf'):
            results.append({"filename": safe_display_filename(file.filename), "error": "只支持PDF格式"})
            continue

        content = await read_upload_file(file, DEFAULT_MAX_UPLOAD_BYTES)

        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            ocr_result = parse_contract_pdf(tmp_path)
        except Exception as e:
            ocr_result = {'name': '', 'id_card': '', 'daily_wage': None,
                         'template_valid': False, 'missing_keywords': [], 'error': str(e)}
        finally:
            os.unlink(tmp_path)

        worker_id = None
        if ocr_result.get('id_card'):
            worker = db.query(Worker).filter(Worker.id_card == ocr_result['id_card']).first()
            if worker:
                worker_id = worker.id

        contract = WorkerContract(
            worker_id=worker_id,
            id_card=ocr_result.get('id_card', ''),
            name=ocr_result.get('name', ''),
            daily_wage=ocr_result.get('daily_wage'),
            original_filename=safe_display_filename(file.filename),
            template_valid=ocr_result.get('template_valid', False),
            missing_keywords=','.join(ocr_result.get('missing_keywords', [])),
            ocr_status='failed' if ocr_result.get('error') else 'done',
            ocr_error=ocr_result.get('error'),
            uploaded_by=current_user.id,
        )
        db.add(contract)
        db.flush()

        file_path = _save_file(content, file.filename, contract.id)
        contract.file_path = file_path
        db.commit()
        db.refresh(contract)

        results.append(_contract_to_dict(contract, db))

    return {"uploaded": len(results), "results": results}


@router.get("/stats")
def contract_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """协议统计"""
    from sqlalchemy import text
    query = _visible_contract_query(db, current_user)
    total = query.count()
    matched = query.filter(WorkerContract.worker_id.isnot(None)).count()
    invalid_template = query.filter(
        WorkerContract.template_valid == False  # noqa: E712
    ).count()
    if current_user.role in ("admin", "operator"):
        no_contract = db.execute(text(
            "SELECT COUNT(*) FROM workers w WHERE NOT EXISTS "
            "(SELECT 1 FROM worker_contracts c WHERE c.worker_id = w.id)"
        )).scalar()
    elif current_user.team_id:
        no_contract = db.execute(text(
            "SELECT COUNT(DISTINCT w.id) FROM workers w "
            "JOIN worker_bank_info b ON b.worker_id = w.id "
            "WHERE b.team_id = :team_id AND NOT EXISTS "
            "(SELECT 1 FROM worker_contracts c WHERE c.worker_id = w.id)"
        ), {"team_id": current_user.team_id}).scalar()
    else:
        no_contract = 0
    return {
        "total": total,
        "matched": matched,
        "unmatched": total - matched,
        "invalid_template": invalid_template,
        "workers_without_contract": no_contract,
    }


@router.post("/bulk-delete")
def bulk_delete_contracts(
    body: BulkDeleteRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量删除用工协议（管理员）"""
    import shutil
    deleted = 0
    for cid in body.ids:
        contract = db.query(WorkerContract).filter(WorkerContract.id == cid).first()
        if not contract:
            continue
        sub_dir = os.path.join(CONTRACTS_DIR, str(cid))
        if os.path.exists(sub_dir):
            shutil.rmtree(sub_dir)
        db.delete(contract)
        deleted += 1
    db.commit()
    return {"deleted": deleted}


@router.post("/check-missing")
def check_missing_contracts(
    body: CheckMissingRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """检查哪些文件名在数据库中不存在"""
    existing = {
        row[0] for row in db.query(WorkerContract.original_filename).all()
        if row[0]
    }
    missing = [f for f in body.filenames if f not in existing]
    return {
        "missing": missing,
        "total": len(body.filenames),
        "found": len(body.filenames) - len(missing),
        "missing_count": len(missing),
    }


@router.get("")
def list_contracts(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用工协议列表"""
    query = _visible_contract_query(db, current_user)

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
    elif status == 'unrecognized':
        query = query.filter(
            (WorkerContract.name == '') | WorkerContract.name.is_(None) |
            (WorkerContract.id_card == '') | WorkerContract.id_card.is_(None)
        )
    elif status == 'duplicate':
        dup_ids = (
            db.query(WorkerContract.id_card)
            .filter(WorkerContract.id_card != '', WorkerContract.id_card.isnot(None))
            .group_by(WorkerContract.id_card)
            .having(func.count() > 1)
            .subquery()
        )
        query = query.filter(WorkerContract.id_card.in_(dup_ids))

    contracts = query.order_by(WorkerContract.uploaded_at.desc()).all()
    return [_contract_to_dict(c, db) for c in contracts]


@router.put("/{contract_id}")
def edit_contract(
    contract_id: int,
    body: EditContractRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """手动编辑协议姓名/身份证（管理员）"""
    contract = db.query(WorkerContract).filter(WorkerContract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="协议不存在")

    if body.name is not None:
        contract.name = body.name.strip()
    if body.id_card is not None:
        id_card = body.id_card.strip().upper()
        contract.id_card = id_card
        worker = db.query(Worker).filter(Worker.id_card == id_card).first()
        contract.worker_id = worker.id if worker else None

    db.commit()
    db.refresh(contract)
    return _contract_to_dict(contract, db)


@router.post("/{contract_id}/reocr")
def reocr_contract(
    contract_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """对已存储的PDF重新运行OCR（管理员）"""
    from services.ocr import parse_contract_pdf

    contract = db.query(WorkerContract).filter(WorkerContract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="协议不存在")
    if not contract.file_path or not os.path.exists(contract.file_path):
        raise HTTPException(status_code=400, detail="原始PDF文件不存在，请重新上传")

    try:
        ocr_result = parse_contract_pdf(contract.file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR失败: {e}")

    _apply_ocr_result(contract, ocr_result, db)
    db.commit()
    db.refresh(contract)
    return _contract_to_dict(contract, db)


@router.post("/{contract_id}/replace")
async def replace_contract_file(
    contract_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """替换协议PDF并重新OCR（管理员）"""
    from services.ocr import parse_contract_pdf
    import tempfile

    contract = db.query(WorkerContract).filter(WorkerContract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="协议不存在")
    if not (file.filename or '').lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持PDF格式")

    content = await read_upload_file(file, DEFAULT_MAX_UPLOAD_BYTES)

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        ocr_result = parse_contract_pdf(tmp_path)
    except Exception as e:
        ocr_result = {'name': '', 'id_card': '', 'daily_wage': None,
                     'template_valid': False, 'missing_keywords': [], 'error': str(e)}
    finally:
        os.unlink(tmp_path)

    file_path = _save_file(content, file.filename, contract_id)
    contract.file_path = file_path
    contract.original_filename = safe_display_filename(file.filename)
    _apply_ocr_result(contract, ocr_result, db)
    db.commit()
    db.refresh(contract)
    return _contract_to_dict(contract, db)


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
    contracts = _visible_contract_query(db, current_user).filter(
        WorkerContract.id_card == id_card
    ).all()
    return [_contract_to_dict(c, db) for c in contracts]
