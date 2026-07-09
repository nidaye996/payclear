"""
月度提交路由：上传表格、解析、触发核对
"""
import os
import json
import shutil
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, BackgroundTasks
from sqlalchemy.orm import Session

from database import get_db, get_bank_db
from models import MonthlySubmission, SubmissionFile, User
from schemas import SubmissionOut, SubmissionCreate
from services.parser import parse_file
from services.checker import run_check
from routers.auth import get_current_user, require_admin
from security import DEFAULT_MAX_UPLOAD_BYTES, read_upload_file, safe_display_filename

router = APIRouter(prefix="/submissions", tags=["月度提交"])

# 上传文件存储目录
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 允许的文件类型
VALID_FILE_TYPES = {'实名制表', '工资表', '支付明细', '考勤表'}
ALLOWED_EXTENSIONS = {'.docx', '.xlsx', '.xls'}


def get_upload_path(submission_id: int, filename: str) -> str:
    """生成上传文件存储路径"""
    sub_dir = os.path.join(UPLOAD_DIR, str(submission_id))
    os.makedirs(sub_dir, exist_ok=True)
    return os.path.join(sub_dir, filename)


def _check_submission_permission(user: User, team_id: int):
    """检查提交权限"""
    if user.role not in ("admin", "operator") and user.team_id != team_id:
        raise HTTPException(status_code=403, detail="无权操作此队伍的提交")


# ==================== 路由 ====================

@router.get("", response_model=List[SubmissionOut])
def list_submissions(
    team_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    is_historical: Optional[bool] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取提交记录列表"""
    query = db.query(MonthlySubmission)

    if current_user.role not in ("admin", "operator"):
        query = query.filter(MonthlySubmission.team_id == current_user.team_id)
    elif team_id:
        query = query.filter(MonthlySubmission.team_id == team_id)

    if year:
        query = query.filter(MonthlySubmission.year == year)
    if month:
        query = query.filter(MonthlySubmission.month == month)
    if is_historical is not None:
        query = query.filter(MonthlySubmission.is_historical == is_historical)

    submissions = query.order_by(
        MonthlySubmission.year.desc(),
        MonthlySubmission.month.desc()
    ).all()

    return [SubmissionOut.model_validate(s) for s in submissions]


@router.post("/create")
def create_submission(
    team_id: int = Form(...),
    year: int = Form(...),
    month: int = Form(...),
    is_historical: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建一个月度提交记录（不上传文件）"""
    _check_submission_permission(current_user, team_id)

    # 检查是否已存在（非历史数据）
    if not is_historical:
        existing = db.query(MonthlySubmission).filter(
            MonthlySubmission.team_id == team_id,
            MonthlySubmission.year == year,
            MonthlySubmission.month == month,
            MonthlySubmission.is_historical == False,
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"{year}年{month}月已有提交记录（ID: {existing.id}），请直接上传文件到该记录"
            )

    submission = MonthlySubmission(
        team_id=team_id,
        year=year,
        month=month,
        submitted_by=current_user.id,
        status='pending',
        is_historical=is_historical,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    return {"id": submission.id, "message": "提交记录已创建"}


@router.post("/{submission_id}/upload")
async def upload_file(
    submission_id: int,
    file_type: str = Form(..., description="实名制表/工资表/支付明细"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """上传文件到提交记录"""
    # 校验文件类型
    if file_type not in VALID_FILE_TYPES:
        raise HTTPException(status_code=400, detail=f"文件类型无效，只能是: {', '.join(VALID_FILE_TYPES)}")

    # 校验文件扩展名
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"文件格式不支持，请上传 docx 或 xlsx 文件")

    # 获取提交记录
    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == submission_id
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    _check_submission_permission(current_user, submission.team_id)

    # 保存文件
    safe_filename = f"{file_type}_{datetime.now().strftime('%H%M%S')}{ext}"
    file_path = get_upload_path(submission_id, safe_filename)

    with open(file_path, "wb") as f:
        content = await read_upload_file(file, DEFAULT_MAX_UPLOAD_BYTES)
        f.write(content)

    # 解析文件
    parse_result = parse_file(file_path, file_type)

    # 保存解析记录
    sub_file = SubmissionFile(
        submission_id=submission_id,
        file_type=file_type,
        original_filename=safe_display_filename(file.filename),
        file_path=file_path,
        parse_status='done' if not parse_result.get('error') else 'error',
        parse_error=parse_result.get('error'),
        parsed_data=json.dumps(parse_result, ensure_ascii=False),
    )
    db.add(sub_file)
    db.commit()

    return {
        "file_id": sub_file.id,
        "file_type": file_type,
        "workers_found": len(parse_result.get('workers', [])),
        "parse_status": sub_file.parse_status,
        "error": parse_result.get('error'),
    }


@router.post("/{submission_id}/check")
def run_check_submission(
    submission_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    bank_db: Session = Depends(get_bank_db),
):
    """触发核对（生成报告）"""
    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == submission_id
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    _check_submission_permission(current_user, submission.team_id)

    # 检查是否有文件
    files = submission.files
    if not files:
        raise HTTPException(status_code=400, detail="还没有上传任何文件")

    # 检查是否有解析成功的文件
    parsed_files = [f for f in files if f.parse_status == 'done']
    if not parsed_files:
        raise HTTPException(status_code=400, detail="所有文件解析失败，请重新上传")

    # 执行核对
    try:
        submission.status = 'checking'
        db.commit()

        report_data = run_check(submission_id, db, bank_db)

        # 保存报告
        from models import CheckReport
        report = CheckReport(
            submission_id=submission_id,
            report_data=json.dumps(report_data, ensure_ascii=False),
            total_workers=report_data['total_workers'],
            error_count=report_data['error_count'],
            warning_count=report_data['warning_count'],
        )
        db.add(report)

        submission.status = 'done'
        db.commit()

        return {
            "report_id": report.id,
            "total_workers": report_data['total_workers'],
            "error_count": report_data['error_count'],
            "warning_count": report_data['warning_count'],
            "message": "核对完成"
        }

    except Exception as e:
        submission.status = 'error'
        db.commit()
        raise HTTPException(status_code=500, detail=f"核对失败: {str(e)}")



@router.delete("/{submission_id}")
def delete_submission(
    submission_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """删除提交记录（管理员）"""
    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == submission_id
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    # 删除上传的文件
    upload_dir = os.path.join(UPLOAD_DIR, str(submission_id))
    if os.path.exists(upload_dir):
        shutil.rmtree(upload_dir)

    db.delete(submission)
    db.commit()
    return {"message": "提交记录已删除"}


@router.get("/{submission_id}", response_model=SubmissionOut)
def get_submission(
    submission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取提交记录详情"""
    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == submission_id
    ).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    _check_submission_permission(current_user, submission.team_id)
    return SubmissionOut.model_validate(submission)
