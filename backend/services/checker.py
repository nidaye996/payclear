"""
三层核对逻辑服务
1. 三表互相核对（实名制表 vs 工资表 vs 支付明细）
2. 与银行联号库核对
3. 与历史数据库核对
"""
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from models import Worker, WorkerBankInfo, BankRouting, MonthlySubmission, SubmissionFile
from services.parser import is_id_card, is_bank_card, is_phone

logger = logging.getLogger(__name__)


# ==================== 核对主入口 ====================

def run_check(
    submission_id: int,
    db: Session,
    bank_db: Session
) -> Dict[str, Any]:
    """
    执行完整核对流程
    返回报告数据字典
    """
    from models import MonthlySubmission, SubmissionFile

    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == submission_id
    ).first()

    if not submission:
        raise ValueError(f"提交记录不存在: {submission_id}")

    files = db.query(SubmissionFile).filter(
        SubmissionFile.submission_id == submission_id
    ).all()

    # 加载各表数据
    registry_data: List[Dict] = []   # 实名制表
    salary_data: List[Dict] = []     # 工资表
    payment_data: List[Dict] = []    # 支付明细

    for f in files:
        if not f.parsed_data:
            continue
        try:
            parsed = json.loads(f.parsed_data)
            workers = parsed.get('workers', [])
        except Exception:
            continue

        if f.file_type == '实名制表':
            registry_data = workers
        elif f.file_type == '工资表':
            salary_data = workers
        elif f.file_type == '支付明细':
            payment_data = workers

    issues = []
    all_id_cards = set()

    # 构建索引（以身份证为key）
    registry_index = {w['id_card']: w for w in registry_data if w.get('id_card')}
    salary_index = {w['id_card']: w for w in salary_data if w.get('id_card')}
    payment_index = {w['id_card']: w for w in payment_data if w.get('id_card')}

    all_id_cards = (
        set(registry_index.keys()) |
        set(salary_index.keys()) |
        set(payment_index.keys())
    )

    # 第零层：基础格式校验
    format_issues = check_format(
        all_id_cards, registry_index, salary_index, payment_index
    )
    issues.extend(format_issues)

    # 第一层：三表互相核对
    cross_issues = check_cross_tables(
        all_id_cards, registry_index, salary_index, payment_index
    )
    issues.extend(cross_issues)

    # 第二层：联行号核对
    routing_issues = check_routing_numbers(
        all_id_cards, registry_index, salary_index, payment_index, bank_db
    )
    issues.extend(routing_issues)

    # 第三层：历史数据核对
    history_issues = check_history(
        all_id_cards, registry_index, salary_index,
        submission.team_id, submission.year, submission.month, db,
        payment=payment_index
    )
    issues.extend(history_issues)

    # 统计
    error_count = sum(1 for i in issues if i['severity'] == 'error')
    warning_count = sum(1 for i in issues if i['severity'] == 'warning')

    return {
        'total_workers': len(all_id_cards),
        'error_count': error_count,
        'warning_count': warning_count,
        'issues': issues,
        'generated_at': datetime.utcnow().isoformat(),
        'submission_id': submission_id,
    }


# ==================== 第零层：基础格式校验 ====================

def check_format(
    all_id_cards: set,
    registry: Dict[str, Dict],
    salary: Dict[str, Dict],
    payment: Dict[str, Dict],
) -> List[Dict[str, Any]]:
    """
    校验身份证号、银行卡号、手机号的基础格式。
    每个字段只报一次（优先取实名制表，其次工资表，再次支付明细）。
    """
    issues = []
    source_order = [('实名制表', registry), ('工资表', salary), ('支付明细', payment)]

    for id_card in all_id_cards:
        # 找到该工人在某张表里的数据
        worker_data = {}
        worker_source = {}
        for src_name, src in source_order:
            data = src.get(id_card, {})
            for k, v in data.items():
                if v and k not in worker_data:
                    worker_data[k] = v
                    worker_source[k] = src_name

        name = worker_data.get('name', '未知')

        # 校验身份证号本身（用于索引的 id_card 就是当前值）
        if id_card and not is_id_card(id_card):
            issues.append({
                'severity': 'error',
                'issue_type': 'format',
                'worker_name': name,
                'id_card': id_card,
                'field': '身份证号',
                'description': f'身份证号 {id_card} 格式或校验位有误',
                'source_a': f'身份证号: {id_card}',
                'source_b': None,
            })

        # 校验银行卡号
        bank_card = worker_data.get('bank_card', '')
        if bank_card and not is_bank_card(bank_card):
            src = worker_source.get('bank_card', '未知来源')
            issues.append({
                'severity': 'error',
                'issue_type': 'format',
                'worker_name': name,
                'id_card': id_card,
                'field': '银行卡号',
                'description': f'银行卡号 {bank_card} 位数或校验位有误（来自{src}）',
                'source_a': f'{src}: {bank_card}',
                'source_b': None,
            })

        # 校验手机号（必填）
        phone = worker_data.get('phone', '')
        if not phone:
            issues.append({
                'severity': 'error',
                'issue_type': 'format',
                'worker_name': name,
                'id_card': id_card,
                'field': '手机号',
                'description': '手机号未填写',
                'source_a': None,
                'source_b': None,
            })
        elif not is_phone(str(phone)):
            src = worker_source.get('phone', '未知来源')
            issues.append({
                'severity': 'error',
                'issue_type': 'format',
                'worker_name': name,
                'id_card': id_card,
                'field': '手机号',
                'description': f'手机号 {phone} 格式有误（来自{src}）',
                'source_a': f'{src}: {phone}',
                'source_b': None,
            })

    return issues


# ==================== 第一层：三表互相核对 ====================

def check_cross_tables(
    all_id_cards: set,
    registry: Dict[str, Dict],
    salary: Dict[str, Dict],
    payment: Dict[str, Dict]
) -> List[Dict[str, Any]]:
    """
    三表互相核对：同一身份证号，姓名/银行卡号/联行号要一致
    """
    issues = []

    for id_card in all_id_cards:
        r = registry.get(id_card, {})
        s = salary.get(id_card, {})
        p = payment.get(id_card, {})

        worker_name = r.get('name') or s.get('name') or p.get('name') or '未知'

        # 检查哪些表包含该工人
        sources = {
            '实名制表': r if r else None,
            '工资表': s if s else None,
            '支付明细': p if p else None,
        }
        present_sources = {k: v for k, v in sources.items() if v}

        if len(present_sources) < 2:
            # 只在一张表中出现，无法互相核对
            continue

        # 核对字段
        fields_to_check = {
            'name': '姓名',
            'bank_card': '银行卡号',
            'routing_number': '联行号',
        }

        for field, field_label in fields_to_check.items():
            values = {}
            for source_name, data in present_sources.items():
                val = data.get(field)
                if val:
                    values[source_name] = val

            if len(values) < 2:
                continue

            # 检查值是否一致
            unique_values = set(values.values())
            if len(unique_values) > 1:
                source_names = list(values.keys())
                for i in range(len(source_names)):
                    for j in range(i + 1, len(source_names)):
                        va = values[source_names[i]]
                        vb = values[source_names[j]]
                        if va != vb:
                            issues.append({
                                'severity': 'error',
                                'issue_type': 'cross_table',
                                'worker_name': worker_name,
                                'id_card': id_card,
                                'field': field_label,
                                'description': f'{source_names[i]}与{source_names[j]}中{field_label}不一致',
                                'source_a': f'{source_names[i]}: {va}',
                                'source_b': f'{source_names[j]}: {vb}',
                            })

        # 检查是否每张表都有该工人
        if registry and id_card not in registry:
            issues.append({
                'severity': 'warning',
                'issue_type': 'cross_table',
                'worker_name': worker_name,
                'id_card': id_card,
                'field': '出现表格',
                'description': f'工人在实名制表中缺失',
                'source_a': None,
                'source_b': None,
            })
        if salary and id_card not in salary:
            issues.append({
                'severity': 'warning',
                'issue_type': 'cross_table',
                'worker_name': worker_name,
                'id_card': id_card,
                'field': '出现表格',
                'description': f'工人在工资表中缺失',
                'source_a': None,
                'source_b': None,
            })

    return issues


# ==================== 第二层：联行号核对 ====================

def check_routing_numbers(
    all_id_cards: set,
    registry: Dict[str, Dict],
    salary: Dict[str, Dict],
    payment: Dict[str, Dict],
    bank_db: Session
) -> List[Dict[str, Any]]:
    """
    核对联行号是否在联号库中存在
    """
    issues = []

    # 收集所有联行号（附带工人信息）
    routing_to_check: Dict[str, Dict] = {}  # routing -> worker info

    for id_card in all_id_cards:
        for data_dict in (registry, salary, payment):
            data = data_dict.get(id_card, {})
            routing = data.get('routing_number')
            if routing and routing not in routing_to_check:
                routing_to_check[routing] = {
                    'id_card': id_card,
                    'name': data.get('name', '未知'),
                    'bank_name': data.get('bank_name', ''),
                }

    if not routing_to_check:
        return issues

    # 批量查询联号库
    routing_list = list(routing_to_check.keys())
    found_routings = set()

    # SQLite 批量查询
    chunk_size = 500
    for i in range(0, len(routing_list), chunk_size):
        chunk = routing_list[i:i + chunk_size]
        results = bank_db.query(BankRouting.routing_number).filter(
            BankRouting.routing_number.in_(chunk)
        ).all()
        for r in results:
            found_routings.add(r[0])

    # 找出不存在的联行号
    for routing, worker_info in routing_to_check.items():
        if routing not in found_routings:
            issues.append({
                'severity': 'error',
                'issue_type': 'bank_routing',
                'worker_name': worker_info['name'],
                'id_card': worker_info['id_card'],
                'field': '联行号',
                'description': f'联行号 {routing} 在联号库中找不到对应记录',
                'source_a': f'联行号: {routing}',
                'source_b': f'银行名称: {worker_info.get("bank_name", "未知")}',
            })

    return issues


# ==================== 第三层：历史数据核对 ====================

def check_history(
    all_id_cards: set,
    registry: Dict[str, Dict],
    salary: Dict[str, Dict],
    team_id: int,
    year: int,
    month: int,
    db: Session,
    payment: Dict[str, Dict] = None
) -> List[Dict[str, Any]]:
    """
    与历史数据库核对：银行卡号、联行号变化需要标注
    """
    issues = []
    current_period = f"{year}-{month:02d}"

    for id_card in all_id_cards:
        # 获取当前提交的数据，同时记录每个字段来自哪个表
        current_data = {}
        field_sources = {}  # field -> source_name
        source_map = [('实名制表', registry), ('工资表', salary), ('支付明细', payment or {})]

        for source_name, data_dict in source_map:
            data = data_dict.get(id_card, {})
            if data:
                for k, v in data.items():
                    if v and k not in current_data:
                        current_data[k] = v
                        field_sources[k] = source_name

        if not current_data:
            continue

        worker_name = current_data.get('name', '未知')

        # 查询历史最新记录
        history = db.query(WorkerBankInfo).join(Worker).filter(
            Worker.id_card == id_card,
            WorkerBankInfo.team_id == team_id,
            WorkerBankInfo.valid_to.is_(None),
            WorkerBankInfo.status == 'confirmed',  # 只和正式库比对，不用暂存库数据
        ).first()

        if not history:
            continue

        # 核对银行卡号
        current_card = current_data.get('bank_card')
        if current_card and history.bank_card and current_card != history.bank_card:
            src = field_sources.get('bank_card', '本次提交')
            issues.append({
                'severity': 'warning',
                'issue_type': 'history',
                'worker_name': worker_name,
                'id_card': id_card,
                'field': '银行卡号',
                'description': f'银行卡号与历史记录不一致（历史: {history.bank_card}，{src}: {current_card}）',
                'source_a': f'历史记录: {history.bank_card}',
                'source_b': f'{src}: {current_card}',
            })

        # 核对联行号
        current_routing = current_data.get('routing_number')
        if current_routing and history.routing_number and current_routing != history.routing_number:
            src = field_sources.get('routing_number', '本次提交')
            issues.append({
                'severity': 'warning',
                'issue_type': 'history',
                'worker_name': worker_name,
                'id_card': id_card,
                'field': '联行号',
                'description': f'联行号与历史记录不一致（历史: {history.routing_number}，{src}: {current_routing}）',
                'source_a': f'历史记录: {history.routing_number}',
                'source_b': f'{src}: {current_routing}',
            })

        # 核对姓名
        current_name = current_data.get('name')
        if current_name and history.worker and history.worker.name:
            if current_name != history.worker.name:
                src = field_sources.get('name', '本次提交')
                issues.append({
                    'severity': 'error',
                    'issue_type': 'history',
                    'worker_name': worker_name,
                    'id_card': id_card,
                    'field': '姓名',
                    'description': f'姓名与历史记录不一致（历史: {history.worker.name}，{src}: {current_name}）',
                    'source_a': f'历史记录: {history.worker.name}',
                    'source_b': f'{src}: {current_name}',
                })

    return issues


# ==================== 数据库更新 ====================

def update_database_from_submission(
    submission_id: int,
    db: Session,
    period_str: str,  # "YYYY-MM"
    bank_info_status: str = 'confirmed',
) -> Dict[str, Any]:
    """
    将提交数据更新到工人数据库（历史数据导入专用，直接入正式库）
    """
    from models import MonthlySubmission, SubmissionFile

    files = db.query(SubmissionFile).filter(
        SubmissionFile.submission_id == submission_id
    ).all()

    # 合并所有表的工人数据
    all_workers: Dict[str, Dict] = {}
    for f in files:
        if not f.parsed_data:
            continue
        try:
            parsed = json.loads(f.parsed_data)
        except Exception:
            continue
        for worker in parsed.get('workers', []):
            id_card = worker.get('id_card')
            if not id_card:
                continue
            if id_card not in all_workers:
                all_workers[id_card] = {}
            # 合并（后来的覆盖前面的空值）
            for k, v in worker.items():
                if v and k not in all_workers[id_card]:
                    all_workers[id_card][k] = v

    submission = db.query(MonthlySubmission).filter(
        MonthlySubmission.id == submission_id
    ).first()
    team_id = submission.team_id

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
            # 更新手机号
            if data.get('phone') and not worker.phone:
                worker.phone = data['phone']
            updated_count += 1

        # 处理银行信息
        bank_card = data.get('bank_card')
        routing_number = data.get('routing_number')
        bank_name = data.get('bank_name')

        if bank_card or routing_number:
            # 查找当前有效记录
            current_info = db.query(WorkerBankInfo).filter(
                WorkerBankInfo.worker_id == worker.id,
                WorkerBankInfo.team_id == team_id,
                WorkerBankInfo.valid_to.is_(None),
            ).first()

            if current_info:
                # 检查是否有变化
                changed = (
                    (bank_card and current_info.bank_card != bank_card) or
                    (routing_number and current_info.routing_number != routing_number)
                )
                if changed:
                    # 关闭旧记录
                    current_info.valid_to = period_str
                    # 创建新记录
                    new_info = WorkerBankInfo(
                        worker_id=worker.id,
                        team_id=team_id,
                        bank_card=bank_card or current_info.bank_card,
                        bank_name=bank_name or current_info.bank_name,
                        routing_number=routing_number or current_info.routing_number,
                        valid_from=period_str,
                        status=bank_info_status,
                    )
                    db.add(new_info)
            else:
                # 创建首条记录
                new_info = WorkerBankInfo(
                    worker_id=worker.id,
                    team_id=team_id,
                    bank_card=bank_card,
                    bank_name=bank_name,
                    routing_number=routing_number,
                    valid_from=period_str,
                    status=bank_info_status,
                )
                db.add(new_info)

    db.commit()
    return {
        'created': created_count,
        'updated': updated_count,
        'total': len(all_workers)
    }
