"""
报告路由：查看报告、导出Excel
"""
import json
import os
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io

from database import get_db
from models import CheckReport, MonthlySubmission, SubmissionFile, User
from schemas import ReportOut
from routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/reports", tags=["核对报告"])


@router.get("", response_model=List[ReportOut])
def list_reports(
    team_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取报告列表"""
    query = db.query(CheckReport).join(MonthlySubmission)

    if current_user.role not in ("admin", "operator"):
        query = query.filter(MonthlySubmission.team_id == current_user.team_id)
    elif team_id:
        query = query.filter(MonthlySubmission.team_id == team_id)

    if year:
        query = query.filter(MonthlySubmission.year == year)
    if month:
        query = query.filter(MonthlySubmission.month == month)

    reports = query.order_by(CheckReport.generated_at.desc()).all()
    result = []
    for r in reports:
        try:
            report_data = json.loads(r.report_data) if r.report_data else {}
        except Exception:
            report_data = {}

        result.append(ReportOut(
            id=r.id,
            submission_id=r.submission_id,
            generated_at=r.generated_at,
            total_workers=r.total_workers,
            error_count=r.error_count,
            warning_count=r.warning_count,
            issues=report_data.get('issues', []),
        ))
    return result


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取报告详情"""
    report = db.query(CheckReport).filter(CheckReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    # 权限检查
    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == report.submission_id
    ).first()
    if current_user.role not in ("admin", "operator") and submission.team_id != current_user.team_id:
        raise HTTPException(status_code=403, detail="无权查看此报告")

    try:
        report_data = json.loads(report.report_data) if report.report_data else {}
    except Exception:
        report_data = {}

    return ReportOut(
        id=report.id,
        submission_id=report.submission_id,
        generated_at=report.generated_at,
        total_workers=report.total_workers,
        error_count=report.error_count,
        warning_count=report.warning_count,
        issues=report_data.get('issues', []),
    )


@router.get("/{report_id}/export")
def export_report_excel(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导出报告为Excel"""
    report = db.query(CheckReport).filter(CheckReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == report.submission_id
    ).first()

    if current_user.role not in ("admin", "operator") and submission.team_id != current_user.team_id:
        raise HTTPException(status_code=403, detail="无权导出此报告")

    try:
        report_data = json.loads(report.report_data) if report.report_data else {}
    except Exception:
        report_data = {}

    issues = report_data.get('issues', [])

    # 生成Excel
    output = _generate_excel_report(
        issues=issues,
        submission=submission,
        report=report,
        submission_id=report.submission_id,
        db=db,
    )

    filename = f"report_{submission.year}_{submission.month:02d}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


def _generate_excel_report(issues: list, submission, report, submission_id: int = None, db=None) -> io.BytesIO:
    """生成Excel格式的报告"""
    import xlsxwriter

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})

    # 格式定义
    title_fmt = workbook.add_format({
        'bold': True, 'font_size': 14, 'align': 'center',
        'valign': 'vcenter', 'bg_color': '#2E4057', 'font_color': 'white'
    })
    header_fmt = workbook.add_format({
        'bold': True, 'bg_color': '#048A81', 'font_color': 'white',
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    error_fmt = workbook.add_format({
        'bg_color': '#FFE0E0', 'border': 1, 'valign': 'vcenter'
    })
    warning_fmt = workbook.add_format({
        'bg_color': '#FFF3CD', 'border': 1, 'valign': 'vcenter'
    })
    normal_fmt = workbook.add_format({'border': 1, 'valign': 'vcenter'})
    center_fmt = workbook.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter'})

    # ====== 汇总页 ======
    ws_summary = workbook.add_worksheet("核对汇总")
    ws_summary.set_column('A:A', 20)
    ws_summary.set_column('B:B', 30)

    ws_summary.merge_range('A1:B1', '农民工工资核对报告', title_fmt)
    ws_summary.set_row(0, 35)

    summary_data = [
        ('报告ID', str(report.id)),
        ('提交ID', str(submission.id)),
        ('年月', f"{submission.year}年{submission.month}月"),
        ('队伍ID', str(submission.team_id)),
        ('生成时间', report.generated_at.strftime('%Y-%m-%d %H:%M:%S')),
        ('总工人数', str(report.total_workers)),
        ('错误数量', str(report.error_count)),
        ('警告数量', str(report.warning_count)),
    ]

    for row_idx, (label, value) in enumerate(summary_data, start=1):
        ws_summary.write(row_idx, 0, label, header_fmt)
        ws_summary.write(row_idx, 1, value, normal_fmt)

    # ====== 问题明细页 ======
    ws_issues = workbook.add_worksheet("问题明细")
    ws_issues.set_column('A:A', 8)
    ws_issues.set_column('B:B', 10)
    ws_issues.set_column('C:C', 20)
    ws_issues.set_column('D:D', 12)
    ws_issues.set_column('E:E', 12)
    ws_issues.set_column('F:F', 40)
    ws_issues.set_column('G:G', 25)
    ws_issues.set_column('H:H', 25)

    # 标题行
    ws_issues.merge_range('A1:H1', '问题明细', title_fmt)
    ws_issues.set_row(0, 35)

    headers = ['序号', '严重程度', '问题类型', '工人姓名', '身份证号', '问题描述', '来源A', '来源B']
    for col, h in enumerate(headers):
        ws_issues.write(1, col, h, header_fmt)
    ws_issues.set_row(1, 25)

    type_map = {
        'cross_table': '三表核对',
        'bank_routing': '联行号核对',
        'history': '历史数据核对',
    }
    severity_map = {
        'error': '❌ 错误',
        'warning': '⚠️ 警告',
        'info': 'ℹ️ 提示',
    }

    for row_idx, issue in enumerate(issues, start=2):
        severity = issue.get('severity', '')
        fmt = error_fmt if severity == 'error' else (warning_fmt if severity == 'warning' else normal_fmt)

        ws_issues.write(row_idx, 0, row_idx - 1, center_fmt)
        ws_issues.write(row_idx, 1, severity_map.get(severity, severity), fmt)
        ws_issues.write(row_idx, 2, type_map.get(issue.get('issue_type', ''), issue.get('issue_type', '')), fmt)
        ws_issues.write(row_idx, 3, issue.get('worker_name', ''), fmt)
        ws_issues.write(row_idx, 4, issue.get('id_card', ''), fmt)
        ws_issues.write(row_idx, 5, issue.get('description', ''), fmt)
        ws_issues.write(row_idx, 6, issue.get('source_a') or '', fmt)
        ws_issues.write(row_idx, 7, issue.get('source_b') or '', fmt)

    # ====== 三表对比页 ======
    if submission_id is not None and db is not None:
        # 从数据库读取三个文件的解析数据
        files = db.query(SubmissionFile).filter(
            SubmissionFile.submission_id == submission_id
        ).all()

        # 按 file_type 取各表的 workers 列表（若同一类型有多个文件取第一个解析成功的）
        TABLE_TYPES = ["实名制表", "工资表", "支付明细"]
        raw_data: dict[str, list] = {}  # file_type -> list of worker dicts
        for f in files:
            if f.file_type in TABLE_TYPES and f.file_type not in raw_data:
                if f.parsed_data:
                    try:
                        parsed = json.loads(f.parsed_data)
                        raw_data[f.file_type] = parsed.get("workers", [])
                    except Exception:
                        pass

        # 以身份证号为 key，汇总三表数据
        # 结构：id_card -> {"实名制表": {...}, "工资表": {...}, "支付明细": {...}}
        all_id_cards: dict[str, dict] = {}
        for table_type in TABLE_TYPES:
            workers = raw_data.get(table_type, [])
            for w in workers:
                id_card = str(w.get("id_card", "")).strip()
                if not id_card:
                    continue
                if id_card not in all_id_cards:
                    all_id_cards[id_card] = {}
                all_id_cards[id_card][table_type] = {
                    "name": str(w.get("name", "") or "").strip(),
                    "bank_card": str(w.get("bank_card", "") or "").strip(),
                    "routing_number": str(w.get("routing_number", "") or "").strip(),
                }

        # --- 格式 ---
        cmp_title_fmt = workbook.add_format({
            'bold': True, 'font_size': 14, 'align': 'center',
            'valign': 'vcenter', 'bg_color': '#2E4057', 'font_color': 'white'
        })
        cmp_header_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#048A81', 'font_color': 'white',
            'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True
        })
        cmp_ok_fmt = workbook.add_format({
            'bg_color': '#D6F5D6', 'border': 1, 'align': 'center', 'valign': 'vcenter'
        })
        cmp_err_fmt = workbook.add_format({
            'bg_color': '#FFB3B3', 'border': 1, 'valign': 'vcenter'
        })
        cmp_missing_fmt = workbook.add_format({
            'bg_color': '#D9D9D9', 'border': 1, 'align': 'center', 'valign': 'vcenter',
            'font_color': '#666666'
        })
        cmp_normal_fmt = workbook.add_format({
            'border': 1, 'valign': 'vcenter'
        })
        cmp_center_fmt = workbook.add_format({
            'border': 1, 'align': 'center', 'valign': 'vcenter'
        })
        cmp_ok_mark_fmt = workbook.add_format({
            'bg_color': '#D6F5D6', 'border': 1, 'align': 'center', 'valign': 'vcenter',
            'bold': True, 'font_color': '#196F3D'
        })
        cmp_err_mark_fmt = workbook.add_format({
            'bg_color': '#FFB3B3', 'border': 1, 'align': 'center', 'valign': 'vcenter',
            'bold': True, 'font_color': '#7B0000'
        })
        cmp_single_mark_fmt = workbook.add_format({
            'bg_color': '#FFF3CD', 'border': 1, 'align': 'center', 'valign': 'vcenter',
            'bold': True, 'font_color': '#856404'
        })
        # 各字段的"有不一致"红底格式（带 border）
        cmp_err_cell_fmt = workbook.add_format({
            'bg_color': '#FFB3B3', 'border': 1, 'valign': 'vcenter'
        })
        cmp_ok_cell_fmt = workbook.add_format({
            'bg_color': '#D6F5D6', 'border': 1, 'valign': 'vcenter'
        })

        ws_cmp = workbook.add_worksheet("三表对比")

        # 列宽
        ws_cmp.set_column('A:A', 6)   # 序号
        ws_cmp.set_column('B:B', 20)  # 身份证号
        ws_cmp.set_column('C:E', 10)  # 姓名×3
        ws_cmp.set_column('F:H', 20)  # 银行卡×3
        ws_cmp.set_column('I:K', 14)  # 联行号×3
        ws_cmp.set_column('L:L', 10)  # 是否一致

        # 标题
        ws_cmp.merge_range('A1:L1', '三表对比', cmp_title_fmt)
        ws_cmp.set_row(0, 35)

        # 表头（第2行）
        cmp_headers = [
            '序号', '身份证号',
            '姓名\n(实名制表)', '姓名\n(支付表)', '姓名\n(支付明细)',
            '银行卡\n(实名制表)', '银行卡\n(支付表)', '银行卡\n(支付明细)',
            '联行号\n(实名制表)', '联行号\n(支付表)', '联行号\n(支付明细)',
            '是否一致',
        ]
        for col, h in enumerate(cmp_headers):
            ws_cmp.write(1, col, h, cmp_header_fmt)
        ws_cmp.set_row(1, 36)

        # 按身份证号排序，逐行写入
        sorted_id_cards = sorted(all_id_cards.keys())
        for row_idx, id_card in enumerate(sorted_id_cards, start=2):
            tables = all_id_cards[id_card]  # dict: table_type -> {name, bank_card, routing_number}

            def cell_val(table_type: str, field: str) -> str:
                """取某表某字段值，不存在返回 None"""
                t = tables.get(table_type)
                if t is None:
                    return None
                return t.get(field, "")

            # 确定各字段在三表中的值
            fields_by_col = []  # (实名, 工资, 支付) 三元组，共三个字段
            for field in ("name", "bank_card", "routing_number"):
                trio = (
                    cell_val("实名制表", field),
                    cell_val("工资表", field),
                    cell_val("支付明细", field),
                )
                fields_by_col.append(trio)

            # 判断整行一致性
            table_count = len(tables)  # 出现在几个表中

            # 判断每个字段是否存在不一致（只比较实际存在且非空的表）
            # 空字符串表示该表没有此字段（如实名制表/支付表无联行号），不参与比较
            def field_has_mismatch(trio):
                present_vals = [v for v in trio if v is not None and v != ""]
                if len(present_vals) <= 1:
                    return False
                return len(set(present_vals)) > 1

            any_mismatch = any(field_has_mismatch(trio) for trio in fields_by_col)

            if table_count <= 1:
                row_mark = "仅一表"
                row_mark_fmt = cmp_single_mark_fmt
            elif any_mismatch:
                row_mark = "✗"
                row_mark_fmt = cmp_err_mark_fmt
            else:
                row_mark = "✓"
                row_mark_fmt = cmp_ok_mark_fmt

            # 写序号、身份证号
            ws_cmp.write(row_idx, 0, row_idx - 1, cmp_center_fmt)
            ws_cmp.write(row_idx, 1, id_card, cmp_normal_fmt)

            # 写姓名、银行卡、联行号各三列（共9列，从第3列开始）
            col_offset = 2
            for field_idx, trio in enumerate(fields_by_col):
                has_mismatch = field_has_mismatch(trio)
                for tbl_idx, val in enumerate(trio):
                    col = col_offset + field_idx * 3 + tbl_idx
                    if val is None:
                        ws_cmp.write(row_idx, col, "—", cmp_missing_fmt)
                    elif val == "":
                        # 该表存在此工人但无此字段（如实名制表/支付表无联行号），显示灰色
                        ws_cmp.write(row_idx, col, "—", cmp_missing_fmt)
                    elif has_mismatch:
                        ws_cmp.write(row_idx, col, val, cmp_err_cell_fmt)
                    else:
                        ws_cmp.write(row_idx, col, val, cmp_ok_cell_fmt)

            # 写"是否一致"列（第12列，索引11）
            ws_cmp.write(row_idx, 11, row_mark, row_mark_fmt)

    workbook.close()
    output.seek(0)
    return output
