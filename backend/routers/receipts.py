"""
打款回执 PDF 解析路由
POST /api/receipts/upload - 上传并解析打款回执PDF
"""
import os
import re
import json
import logging
import tempfile
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Worker, WorkerBankInfo, PaymentReceipt, User
from routers.auth import get_current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/receipts", tags=["回执管理"])


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    从PDF中提取文本，优先使用 pdfplumber，回退到 pdfminer，再回退到 pdftotext
    """
    # 方式1: pdfplumber
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        if text_parts:
            return "\n".join(text_parts)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfplumber 解析失败: {e}")

    # 方式2: pdfminer.six
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        text = pdfminer_extract(pdf_path)
        if text and text.strip():
            return text
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfminer 解析失败: {e}")

    # 方式3: 系统命令 pdftotext（需要 poppler）
    try:
        import subprocess
        result = subprocess.run(
            ['pdftotext', '-layout', pdf_path, '-'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception as e:
        logger.warning(f"pdftotext 解析失败: {e}")

    raise ValueError("无法解析PDF文件，请确保安装了 pdfplumber 或 pdfminer.six 或 poppler(pdftotext)")


def parse_receipt_text(text: str) -> List[Dict[str, Any]]:
    """
    解析回执文本，提取每笔记录。
    支持格式：序号 对方账号 对方户名 交易金额 状态 [流水号] [用途]
    例如：
      1 6217002300029176258 孙学存 960.00 成功 ...
      2 6212261602028069468 高桂平 6000.00 失败 ...
    """
    records = []

    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 匹配行格式：
        # 数字 + 银行卡号(10-25位数字) + 姓名(2-10个中文字) + 金额(数字.数字) + 状态(成功/失败/退票等)
        # 尝试多种正则模式
        patterns = [
            # 格式1: 序号 账号 姓名 金额 状态
            r'^(\d+)\s+(\d{10,25})\s+([\u4e00-\u9fa5]{2,10})\s+([\d,]+\.?\d*)\s+(成功|失败|退票|冲正|处理中)',
            # 格式2: 账号 姓名 金额 状态（无序号）
            r'^(\d{10,25})\s+([\u4e00-\u9fa5]{2,10})\s+([\d,]+\.?\d*)\s+(成功|失败|退票|冲正|处理中)',
        ]

        matched = False
        for pat in patterns:
            m = re.match(pat, line)
            if m:
                groups = m.groups()
                if len(groups) == 5:
                    # 有序号
                    _, bank_card, name, amount_str, status = groups
                elif len(groups) == 4:
                    # 无序号
                    bank_card, name, amount_str, status = groups
                else:
                    continue

                try:
                    amount = float(amount_str.replace(',', ''))
                except ValueError:
                    amount = 0.0

                records.append({
                    'bank_card': bank_card.strip(),
                    'name': name.strip(),
                    'amount': amount,
                    'status': status.strip(),
                    'is_success': status.strip() == '成功',
                    'raw_line': line,
                })
                matched = True
                break

    return records


@router.post("/upload")
async def upload_receipt(
    file: UploadFile = File(...),
    team_id: int = Form(...),
    submission_id: Optional[int] = Form(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    上传并解析打款回执PDF。
    - 解析PDF文本，提取每笔打款记录
    - 按银行卡号匹配暂存库中的工人
    - 成功的工人自动标记为 confirmed
    - 失败的工人保持 pending
    - 存储到 PaymentReceipt 表
    """
    # 校验文件格式
    filename = file.filename or ''
    ext = os.path.splitext(filename)[1].lower()
    if ext != '.pdf':
        raise HTTPException(status_code=400, detail="请上传 PDF 文件")

    # 临时保存文件
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 提取文本
        try:
            text = extract_text_from_pdf(tmp_path)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        if not text.strip():
            raise HTTPException(status_code=422, detail="PDF文件内容为空或无法提取文本")

        # 解析回执记录
        records = parse_receipt_text(text)

        if not records:
            raise HTTPException(
                status_code=422,
                detail="未能从PDF中解析出有效的打款记录，请检查PDF格式是否符合要求（序号 对方账号 对方户名 交易金额 状态）"
            )

        # 按银行卡号匹配暂存库工人
        success_workers = []
        fail_workers = []
        unmatched = []

        for record in records:
            bank_card = record['bank_card']

            # 查找暂存库中匹配的 WorkerBankInfo
            bank_info = db.query(WorkerBankInfo).filter(
                WorkerBankInfo.bank_card == bank_card,
                WorkerBankInfo.valid_to == None,
            ).first()

            if not bank_info:
                # 未在数据库中匹配到（可能是非暂存工人或卡号不存在）
                unmatched.append({
                    **record,
                    'reason': '未在工人库中匹配到此银行卡号',
                })
                continue

            worker = db.query(Worker).filter(Worker.id == bank_info.worker_id).first()
            worker_info = {
                **record,
                'worker_id': worker.id if worker else None,
                'worker_name_db': worker.name if worker else None,
                'id_card': worker.id_card if worker else None,
                'bank_info_status': bank_info.status,
            }

            if record['is_success']:
                # 成功的工人：自动确认入正式库
                if bank_info.status == 'pending':
                    bank_info.status = 'confirmed'
                success_workers.append(worker_info)
            else:
                # 失败的工人：保持 pending
                fail_workers.append(worker_info)

        db.flush()

        # 存储回执记录
        receipt_data_json = json.dumps({
            'records': records,
            'success_workers': success_workers,
            'fail_workers': fail_workers,
            'unmatched': unmatched,
            'raw_text_length': len(text),
        }, ensure_ascii=False)

        receipt = PaymentReceipt(
            submission_id=submission_id,
            team_id=team_id,
            uploaded_by=current_user.id,
            total_count=len(records),
            success_count=len(success_workers),
            fail_count=len(fail_workers),
            receipt_data=receipt_data_json,
        )
        db.add(receipt)
        db.commit()
        db.refresh(receipt)

        return {
            "receipt_id": receipt.id,
            "total_count": len(records),
            "success_count": len(success_workers),
            "fail_count": len(fail_workers),
            "unmatched_count": len(unmatched),
            "message": f"解析完成：{len(success_workers)} 笔成功已自动入库，{len(fail_workers)} 笔失败保持暂存",
            "success_workers": success_workers,
            "fail_workers": fail_workers,
            "unmatched": unmatched,
        }

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.get("")
def list_receipts(
    team_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """获取回执上传记录列表（管理员）"""
    query = db.query(PaymentReceipt).order_by(PaymentReceipt.uploaded_at.desc())

    if team_id:
        query = query.filter(PaymentReceipt.team_id == team_id)

    total = query.count()
    receipts = query.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for r in receipts:
        uploader = db.query(User).filter(User.id == r.uploaded_by).first()
        items.append({
            "id": r.id,
            "team_id": r.team_id,
            "submission_id": r.submission_id,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "uploaded_by": r.uploaded_by,
            "uploader_name": uploader.username if uploader else "未知",
            "total_count": r.total_count,
            "success_count": r.success_count,
            "fail_count": r.fail_count,
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@router.get("/{receipt_id}")
def get_receipt_detail(
    receipt_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """获取回执详情（管理员）"""
    receipt = db.query(PaymentReceipt).filter(PaymentReceipt.id == receipt_id).first()
    if not receipt:
        raise HTTPException(status_code=404, detail="回执记录不存在")

    data = {}
    if receipt.receipt_data:
        try:
            data = json.loads(receipt.receipt_data)
        except Exception:
            pass

    uploader = db.query(User).filter(User.id == receipt.uploaded_by).first()

    return {
        "id": receipt.id,
        "team_id": receipt.team_id,
        "submission_id": receipt.submission_id,
        "uploaded_at": receipt.uploaded_at.isoformat() if receipt.uploaded_at else None,
        "uploader_name": uploader.username if uploader else "未知",
        "total_count": receipt.total_count,
        "success_count": receipt.success_count,
        "fail_count": receipt.fail_count,
        **data,
    }
