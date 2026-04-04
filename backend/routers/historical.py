"""
历史数据导入路由（两阶段流程：分析 → 确认导入）

第一步 POST /api/historical/analyze：
    上传三张表 + 回执PDF → 解析 + 三表核对 + 回执匹配 → 返回分析结果（不写工人库）

第二步 POST /api/historical/confirm：
    传入 submission_id + 管理员勾选的 approved_id_cards → 写入正式库
"""
import os
import re
import json
import logging
import tempfile

_audit = logging.getLogger("audit")
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User, Worker, WorkerBankInfo, MonthlySubmission, SubmissionFile
from routers.auth import require_admin, require_admin_or_operator
from services.parser import parse_file
from services.checker import check_cross_tables

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/historical", tags=["历史数据导入"])


# ==================== PDF 解析工具 ====================

def extract_text_from_pdf(pdf_path: str) -> str:
    """从PDF提取文本，依次尝试 pdfplumber → pdfminer → pdftotext"""
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        if parts:
            return "\n".join(parts)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfplumber 解析失败: {e}")

    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        text = pdfminer_extract(pdf_path)
        if text and text.strip():
            return text
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfminer 解析失败: {e}")

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

    raise ValueError("无法解析PDF，请确保已安装 pdfplumber 或 pdfminer.six")


def parse_receipt_text(text: str) -> List[Dict[str, Any]]:
    """
    解析回执文本，提取每笔打款记录。
    支持格式：[序号] 银行卡号 姓名 金额 状态
    """
    records = []
    patterns = [
        # 有序号
        r'^(\d+)\s+(\d{10,25})\s+([\u4e00-\u9fa5]{2,10})\s+([\d,]+\.?\d*)\s+(成功|失败|退票|冲正|处理中)',
        # 无序号
        r'^(\d{10,25})\s+([\u4e00-\u9fa5]{2,10})\s+([\d,]+\.?\d*)\s+(成功|失败|退票|冲正|处理中)',
    ]
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        for pat in patterns:
            m = re.match(pat, line)
            if m:
                groups = m.groups()
                if len(groups) == 5:
                    _, bank_card, name, amount_str, status = groups
                else:
                    bank_card, name, amount_str, status = groups
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
                })
                break
    return records


# ==================== 第一步：分析 ====================

@router.post("/analyze")
async def analyze_historical(
    team_id: int = Form(...),
    year: int = Form(...),
    month: int = Form(...),
    file_type_0: str = Form(...),
    file_0: UploadFile = File(...),
    file_type_1: Optional[str] = Form(None),
    file_1: Optional[UploadFile] = File(None),
    file_type_2: Optional[str] = Form(None),
    file_2: Optional[UploadFile] = File(None),
    receipt_file: UploadFile = File(...),
    current_user: User = Depends(require_admin_or_operator),
    db: Session = Depends(get_db),
):
    """
    第一步：解析三张表 + 回执PDF，返回分析结果。
    结果暂存到 monthly_submissions（status='analyzing'），不修改工人库。
    """
    tmp_files = []
    try:
        # ---- 保存并解析三张表 ----
        upload_pairs = [
            (file_type_0, file_0),
            (file_type_1, file_1),
            (file_type_2, file_2),
        ]
        files_to_process = []
        for file_type, upload_file in upload_pairs:
            if not file_type or not upload_file:
                continue
            ext = os.path.splitext(upload_file.filename or '')[1].lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(await upload_file.read())
                tmp_files.append(tmp.name)
                files_to_process.append({
                    'path': tmp.name,
                    'type': file_type,
                    'original_name': upload_file.filename,
                })

        if not files_to_process:
            raise HTTPException(status_code=400, detail="至少上传一个表格文件")

        # ---- 保存并解析回执PDF ----
        ext = os.path.splitext(receipt_file.filename or '')[1].lower()
        if ext != '.pdf':
            raise HTTPException(status_code=400, detail="回执单请上传PDF文件")
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(await receipt_file.read())
            tmp_files.append(tmp.name)
            receipt_tmp_path = tmp.name

        try:
            receipt_text = extract_text_from_pdf(receipt_tmp_path)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        receipt_records = parse_receipt_text(receipt_text)
        if not receipt_records:
            raise HTTPException(
                status_code=422,
                detail="未能从回执PDF中解析出打款记录，请确认格式（序号 账号 姓名 金额 状态）"
            )

        # 银行卡号 → 回执记录的映射（同卡号取第一条）
        receipt_map: Dict[str, Dict] = {}
        for r in receipt_records:
            if r['bank_card'] not in receipt_map:
                receipt_map[r['bank_card']] = r

        # ---- 创建提交记录（暂存，不入工人库）----
        submission = MonthlySubmission(
            team_id=team_id,
            year=year,
            month=month,
            status='analyzing',
            is_historical=True,
            submitted_by=current_user.id,
        )
        db.add(submission)
        db.flush()

        # ---- 解析三张表并存入 SubmissionFile ----
        registry_data, salary_data, payment_data = [], [], []
        for fi in files_to_process:
            result = parse_file(fi['path'], fi['type'])
            db.add(SubmissionFile(
                submission_id=submission.id,
                file_type=fi['type'],
                original_filename=fi['original_name'],
                file_path=fi['path'],
                parse_status='done' if not result.get('error') else 'error',
                parse_error=result.get('error'),
                parsed_data=json.dumps(result, ensure_ascii=False),
            ))
            workers = result.get('workers', [])
            if fi['type'] == '实名制表':
                registry_data = workers
            elif fi['type'] == '工资表':
                salary_data = workers
            elif fi['type'] == '支付明细':
                payment_data = workers

        # 把回执数据也存一份（confirm步骤不需要，但留存备查）
        db.add(SubmissionFile(
            submission_id=submission.id,
            file_type='回执单',
            original_filename=receipt_file.filename,
            parse_status='done',
            parsed_data=json.dumps({'records': receipt_records}, ensure_ascii=False),
        ))
        db.commit()

        # ---- 三表交叉核对 ----
        registry_index = {w['id_card']: w for w in registry_data if w.get('id_card')}
        salary_index   = {w['id_card']: w for w in salary_data   if w.get('id_card')}
        payment_index  = {w['id_card']: w for w in payment_data  if w.get('id_card')}
        all_id_cards   = set(registry_index) | set(salary_index) | set(payment_index)

        cross_issues = check_cross_tables(all_id_cards, registry_index, salary_index, payment_index)

        # ---- 合并工人数据 + 与回执匹配 ----
        workers_result = []
        for id_card in all_id_cards:
            merged: Dict[str, Any] = {}
            for src in (registry_index, salary_index, payment_index):
                for k, v in src.get(id_card, {}).items():
                    if v and k not in merged:
                        merged[k] = v

            name      = merged.get('name', '未知')
            bank_card = merged.get('bank_card', '')

            if bank_card and bank_card in receipt_map:
                rec = receipt_map[bank_card]
                if rec['is_success']:
                    receipt_status   = 'success'
                    default_approved = True
                else:
                    receipt_status   = 'failed'
                    default_approved = False   # 打款失败，禁止导入
                receipt_amount = rec['amount']
            elif bank_card:
                receipt_status   = 'not_found'
                default_approved = True        # 未找到，管理员决定（Option B）
                receipt_amount   = None
            else:
                receipt_status   = 'no_card'
                default_approved = True        # 无银行卡，警告但允许
                receipt_amount   = None

            workers_result.append({
                'id_card':          id_card,
                'name':             name,
                'bank_card':        bank_card,
                'receipt_status':   receipt_status,
                'receipt_amount':   receipt_amount,
                'default_approved': default_approved,
            })

        # 排序：failed 排最前，让管理员优先关注
        _order = {'failed': 0, 'not_found': 1, 'no_card': 2, 'success': 3}
        workers_result.sort(key=lambda w: _order.get(w['receipt_status'], 99))

        summary = {
            'total':           len(workers_result),
            'receipt_success': sum(1 for w in workers_result if w['receipt_status'] == 'success'),
            'receipt_failed':  sum(1 for w in workers_result if w['receipt_status'] == 'failed'),
            'not_in_receipt':  sum(1 for w in workers_result if w['receipt_status'] == 'not_found'),
            'no_card':         sum(1 for w in workers_result if w['receipt_status'] == 'no_card'),
        }

        return {
            'submission_id': submission.id,
            'period':        f'{year}-{month:02d}',
            'receipt_total': len(receipt_records),
            'workers':       workers_result,
            'cross_issues':  cross_issues,
            'summary':       summary,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"历史数据分析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except Exception:
                pass


# ==================== 第二步：确认导入 ====================

class ConfirmRequest(BaseModel):
    submission_id: int
    approved_id_cards: List[str]


@router.post("/confirm")
def confirm_historical(
    req: ConfirmRequest,
    current_user: User = Depends(require_admin_or_operator),
    db: Session = Depends(get_db),
):
    """
    第二步：将 approved_id_cards 中的工人写入正式库（status='confirmed'）。
    """
    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == req.submission_id,
        MonthlySubmission.is_historical == True,
        MonthlySubmission.status == 'analyzing',
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="分析记录不存在或已导入过")

    approved_set = set(req.approved_id_cards)
    if not approved_set:
        raise HTTPException(status_code=400, detail="未选择任何工人")

    period_str = f"{submission.year}-{submission.month:02d}"
    team_id    = submission.team_id

    # 从 SubmissionFile 加载三张表（排除回执单）
    files = db.query(SubmissionFile).filter(
        SubmissionFile.submission_id == req.submission_id,
        SubmissionFile.file_type != '回执单',
        SubmissionFile.parse_status == 'done',
    ).all()

    # 合并所有表的工人数据，只保留 approved 的
    all_workers: Dict[str, Dict] = {}
    for f in files:
        if not f.parsed_data:
            continue
        try:
            parsed = json.loads(f.parsed_data)
        except Exception:
            continue
        for w in parsed.get('workers', []):
            id_card = w.get('id_card')
            if not id_card or id_card not in approved_set:
                continue
            if id_card not in all_workers:
                all_workers[id_card] = {}
            for k, v in w.items():
                if v and k not in all_workers[id_card]:
                    all_workers[id_card][k] = v

    created_count = 0
    updated_count = 0

    for id_card, data in all_workers.items():
        name = data.get('name')
        if not name:
            continue

        # 查找或创建工人
        worker = db.query(Worker).filter(Worker.id_card == id_card).first()
        if not worker:
            worker = Worker(
                name=name,
                id_card=id_card,
                phone=data.get('phone'),
            )
            db.add(worker)
            db.flush()
            created_count += 1
        else:
            if data.get('phone') and not worker.phone:
                worker.phone = data['phone']
            updated_count += 1

        # 处理银行信息
        bank_card      = data.get('bank_card')
        routing_number = data.get('routing_number')
        bank_name      = data.get('bank_name')

        if bank_card or routing_number:
            current_info = db.query(WorkerBankInfo).filter(
                WorkerBankInfo.worker_id == worker.id,
                WorkerBankInfo.team_id   == team_id,
                WorkerBankInfo.valid_to.is_(None),
            ).first()

            if current_info:
                changed = (
                    (bank_card      and current_info.bank_card      != bank_card) or
                    (routing_number and current_info.routing_number != routing_number)
                )
                if changed:
                    current_info.valid_to = period_str
                    db.add(WorkerBankInfo(
                        worker_id      = worker.id,
                        team_id        = team_id,
                        bank_card      = bank_card      or current_info.bank_card,
                        bank_name      = bank_name      or current_info.bank_name,
                        routing_number = routing_number or current_info.routing_number,
                        valid_from     = period_str,
                        status         = 'confirmed',
                    ))
            else:
                db.add(WorkerBankInfo(
                    worker_id      = worker.id,
                    team_id        = team_id,
                    bank_card      = bank_card,
                    bank_name      = bank_name,
                    routing_number = routing_number,
                    valid_from     = period_str,
                    status         = 'confirmed',
                ))

    submission.status = 'done'
    db.commit()

    _audit.info("HISTORICAL_IMPORT team_id=%s period=%s created=%d updated=%d by=%s",
                team_id, period_str, created_count, updated_count, current_user.username)
    return {
        'created': created_count,
        'updated': updated_count,
        'total':   created_count + updated_count,
        'period':  period_str,
    }
