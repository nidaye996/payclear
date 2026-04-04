"""
历史数据批量导入 & 银行联号库导入服务
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from sqlalchemy.orm import Session

from services.parser import parse_file
from services.checker import update_database_from_submission
from models import BankRouting, MonthlySubmission, SubmissionFile, Worker, WorkerBankInfo

logger = logging.getLogger(__name__)


def import_bank_routing_file(file_path: str, bank_db: Session) -> Dict[str, Any]:
    """
    导入银行联号库
    支持 Excel 格式，15万条记录
    """
    import openpyxl

    logger.info(f"开始导入银行联号库: {file_path}")

    # 清空现有数据
    bank_db.query(BankRouting).delete()
    bank_db.commit()

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active

    # 找表头行
    header_row = None
    header_idx = 0
    col_map = {}

    # 关键词映射
    col_keywords = {
        '机构全称': 'institution_name',
        '银行机构': 'institution_name',
        '联行号': 'routing_number',
        '开户行名称': 'branch_name',
        '开户行': 'branch_name',
        '省': 'province',
        '市': 'city',
        '行别': 'bank_type_code',
    }

    rows_iter = ws.iter_rows(values_only=True)
    total_inserted = 0
    batch: List[Dict] = []
    batch_size = 2000

    for row_idx, row in enumerate(rows_iter):
        if not any(v is not None for v in row):
            continue

        if header_row is None:
            # 检查是否为表头行
            row_str = [str(v).strip() if v else '' for v in row]
            for col_i, cell_val in enumerate(row_str):
                for kw, field in col_keywords.items():
                    if kw in cell_val and field not in col_map.values():
                        col_map[col_i] = field
                        break

            if col_map and '联行号' in ' '.join(str(v) for v in row if v):
                header_row = row_idx
                logger.info(f"找到表头行: {row_idx}, 列映射: {col_map}")
            continue

        # 解析数据行
        record = {}
        for col_i, field in col_map.items():
            if col_i < len(row):
                val = row[col_i]
                record[field] = str(val).strip() if val is not None else ''

        routing = record.get('routing_number', '').strip()
        if not routing or not routing.isdigit() or len(routing) != 12:
            continue

        batch.append({
            'institution_name': record.get('institution_name', '')[:200],
            'routing_number': routing,
            'branch_name': record.get('branch_name', '')[:200],
            'province': record.get('province', '')[:50],
            'city': record.get('city', '')[:50],
            'bank_type_code': record.get('bank_type_code', '')[:20],
        })

        if len(batch) >= batch_size:
            bank_db.bulk_insert_mappings(BankRouting, batch)
            bank_db.commit()
            total_inserted += len(batch)
            batch = []
            logger.info(f"已导入 {total_inserted} 条...")

    # 插入剩余
    if batch:
        bank_db.bulk_insert_mappings(BankRouting, batch)
        bank_db.commit()
        total_inserted += len(batch)

    wb.close()
    logger.info(f"银行联号库导入完成，共 {total_inserted} 条")

    return {
        'success': True,
        'total_inserted': total_inserted,
        'message': f'成功导入 {total_inserted} 条银行联号记录'
    }


def import_historical_batch(
    files_info: List[Dict[str, Any]],
    team_id: int,
    year: int,
    month: int,
    db: Session
) -> Dict[str, Any]:
    """
    批量导入历史数据
    files_info: [{'path': str, 'type': '实名制表'/'工资表'/'支付明细'}]
    """
    # 创建历史提交记录
    submission = MonthlySubmission(
        team_id=team_id,
        year=year,
        month=month,
        status='checking',
        is_historical=True,
    )
    db.add(submission)
    db.flush()

    parse_results = []
    all_success = True

    for file_info in files_info:
        file_path = file_info['path']
        file_type = file_info['type']
        original_name = os.path.basename(file_path)

        # 解析文件
        result = parse_file(file_path, file_type)

        sub_file = SubmissionFile(
            submission_id=submission.id,
            file_type=file_type,
            original_filename=original_name,
            file_path=file_path,
            parse_status='done' if not result['error'] else 'error',
            parse_error=result.get('error'),
            parsed_data=json.dumps(result, ensure_ascii=False),
        )
        db.add(sub_file)

        parse_results.append({
            'file': original_name,
            'type': file_type,
            'workers_found': len(result.get('workers', [])),
            'error': result.get('error'),
        })

        if result.get('error'):
            all_success = False

    db.flush()

    # 更新数据库（历史数据直接入正式库，status='confirmed'）
    period_str = f"{year}-{month:02d}"
    update_result = update_database_from_submission(submission.id, db, period_str, bank_info_status='confirmed')

    submission.status = 'done' if all_success else 'error'
    db.commit()

    return {
        'submission_id': submission.id,
        'parse_results': parse_results,
        'db_update': update_result,
        'period': period_str,
        'all_success': all_success,
    }
