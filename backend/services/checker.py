"""
核对逻辑服务
1. 四表互相核对（实名制表 vs 工资表 vs 支付明细 vs 考勤表）
2. 与银行联号库核对（联行号 + 银行机构全称 + 开户行名称 三字段全查）
3. BIN码核查（卡号前缀对应银行 vs 开户行名称）
4. 工资金额逻辑核查（应发-代扣=实发，明细表金额=实发）
5. 出勤天数一致性核查（考勤表 vs 工资表）
6. 与历史数据库核对
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
    attendance_data: List[Dict] = [] # 考勤表
    station_meeting: Dict = {}        # 站班会数据

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
        elif f.file_type == '考勤表':
            attendance_data = workers
        elif f.file_type == '站班会数据':
            try:
                parsed_full = json.loads(f.parsed_data)
                station_meeting = parsed_full.get('station_meeting', {})
            except Exception:
                pass

    # 构建索引（以身份证为key）
    registry_index  = {w['id_card']: w for w in registry_data  if w.get('id_card')}
    salary_index    = {w['id_card']: w for w in salary_data    if w.get('id_card')}
    payment_index   = {w['id_card']: w for w in payment_data   if w.get('id_card')}
    # 考勤表以姓名为key（考勤表通常无身份证）
    attendance_by_name = {w['name']: w for w in attendance_data if w.get('name')}

    all_id_cards = (
        set(registry_index.keys()) |
        set(salary_index.keys()) |
        set(payment_index.keys())
    )

    issues = []

    # 第零层：基础格式校验
    issues.extend(check_format(all_id_cards, registry_index, salary_index, payment_index))

    # 第一层：四表人员名单 + 关键字段一致性核对
    issues.extend(check_cross_tables(
        all_id_cards, registry_index, salary_index, payment_index, attendance_by_name
    ))

    # 第二层：联行号核对（三字段全查：联行号 + 银行机构全称 + 开户行名称）
    issues.extend(check_routing_numbers(
        all_id_cards, registry_index, salary_index, payment_index, bank_db
    ))

    # 第三层：BIN码核查（卡号对应银行 vs 开户行名称）
    issues.extend(check_bin_codes(all_id_cards, registry_index, salary_index, payment_index))

    # 第四层：工资金额逻辑核查
    issues.extend(check_salary_logic(all_id_cards, salary_index, payment_index))

    # 第五层：出勤天数一致性（考勤表 vs 工资表）
    if attendance_by_name:
        issues.extend(check_attendance_consistency(all_id_cards, salary_index, attendance_by_name))

    # 第六层：站班会核查（考勤表 vs 站班会原始记录）
    if station_meeting and attendance_by_name:
        issues.extend(check_station_meeting(attendance_by_name, station_meeting))

    # 第七层：用工协议核查（合同存在性 + 日工资校验）
    issues.extend(check_contracts(all_id_cards, salary_index, attendance_by_name, db))

    # 第八层：历史数据核对
    issues.extend(check_history(
        all_id_cards, registry_index, salary_index,
        submission.team_id, submission.year, submission.month, db,
        payment=payment_index
    ))

    error_count   = sum(1 for i in issues if i['severity'] == 'error')
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
    payment: Dict[str, Dict],
    attendance_by_name: Dict[str, Dict] = None,
) -> List[Dict[str, Any]]:
    """
    四表互相核对：
    - 实名制表 / 工资表 / 支付明细：姓名、身份证、银行卡号、开户银行、联行号必须一致
    - 考勤表：人员名单必须与其他三表一致（考勤表通常只有姓名，无银行信息）
    """
    issues = []
    attendance_by_name = attendance_by_name or {}

    # 考勤表姓名集合
    attendance_names = set(attendance_by_name.keys())

    for id_card in all_id_cards:
        r = registry.get(id_card, {})
        s = salary.get(id_card, {})
        p = payment.get(id_card, {})

        worker_name = r.get('name') or s.get('name') or p.get('name') or '未知'

        sources = {
            '实名制表': r if r else None,
            '工资表':   s if s else None,
            '支付明细': p if p else None,
        }
        present_sources = {k: v for k, v in sources.items() if v}

        # ── 三表关键字段一致性 ──
        fields_to_check = {
            'name':           '姓名',
            'bank_card':      '银行卡号',
            'bank_name':      '开户银行',
            'routing_number': '联行号',
        }

        for field, field_label in fields_to_check.items():
            values = {
                src: data[field]
                for src, data in present_sources.items()
                if data.get(field)
            }
            if len(values) < 2:
                continue
            unique_vals = set(values.values())
            if len(unique_vals) > 1:
                src_list = list(values.keys())
                for i in range(len(src_list)):
                    for j in range(i + 1, len(src_list)):
                        va, vb = values[src_list[i]], values[src_list[j]]
                        if va != vb:
                            issues.append({
                                'severity': 'error',
                                'issue_type': 'cross_table',
                                'worker_name': worker_name,
                                'id_card': id_card,
                                'field': field_label,
                                'description': f'{src_list[i]}与{src_list[j]}中{field_label}不一致',
                                'source_a': f'{src_list[i]}: {va}',
                                'source_b': f'{src_list[j]}: {vb}',
                            })

        # ── 表格出现完整性检查 ──
        for tbl_name, tbl_index in [('实名制表', registry), ('工资表', salary), ('支付明细', payment)]:
            if tbl_index and id_card not in tbl_index:
                issues.append({
                    'severity': 'warning',
                    'issue_type': 'cross_table',
                    'worker_name': worker_name,
                    'id_card': id_card,
                    'field': '出现表格',
                    'description': f'工人在{tbl_name}中缺失',
                    'source_a': None,
                    'source_b': None,
                })

        # ── 考勤表人员核对 ──
        if attendance_names and worker_name and worker_name not in attendance_names:
            issues.append({
                'severity': 'warning',
                'issue_type': 'cross_table',
                'worker_name': worker_name,
                'id_card': id_card,
                'field': '出现表格',
                'description': '工人在考勤表中缺失',
                'source_a': None,
                'source_b': None,
            })

    # ── 反向：考勤表中有、其他三表中没有 ──
    if attendance_names:
        three_table_names = {
            w.get('name') for idx in (registry, salary, payment)
            for w in idx.values() if w.get('name')
        }
        for att_name in attendance_names:
            if att_name not in three_table_names:
                issues.append({
                    'severity': 'warning',
                    'issue_type': 'cross_table',
                    'worker_name': att_name,
                    'id_card': '—',
                    'field': '出现表格',
                    'description': f'考勤表中有"{att_name}"，但在支付表/实名制表/支付明细中未找到',
                    'source_a': '考勤表: 存在',
                    'source_b': '其他三表: 缺失',
                })

    return issues


# ==================== 第二层：联行号核对（三字段全查） ====================

def check_routing_numbers(
    all_id_cards: set,
    registry: Dict[str, Dict],
    salary: Dict[str, Dict],
    payment: Dict[str, Dict],
    bank_db: Session
) -> List[Dict[str, Any]]:
    """
    核对联行号三字段（必须与联号库完全一致）：
    1. 收款银行联行号：必须在联号库中存在
    2. 收款方开户行名称（bank_name）：必须与联号库 branch_name 完全一致
    3. 收款方银行名称（bank_inst_name，仅支付明细表有此字段）：必须与联号库 institution_name 完全一致
    """
    issues = []

    # 收集每个工人的联行号相关数据（每人只收集一次）
    worker_routing_data = []

    for id_card in all_id_cards:
        r = registry.get(id_card, {})
        s = salary.get(id_card, {})
        p = payment.get(id_card, {})

        name = r.get('name') or s.get('name') or p.get('name') or '未知'

        # 联行号：三表应一致，取第一个有值的
        routing = r.get('routing_number') or s.get('routing_number') or p.get('routing_number')
        if not routing:
            continue

        # 开户行名称（branch_name 对应字段）：来自任意表
        bank_name = r.get('bank_name') or s.get('bank_name') or p.get('bank_name') or ''

        # 收款方银行名称（institution_name 对应字段）：仅支付明细表有此字段
        bank_inst_name = p.get('bank_inst_name', '')

        worker_routing_data.append({
            'id_card': id_card,
            'name': name,
            'routing': routing,
            'bank_name': bank_name,
            'bank_inst_name': bank_inst_name,
        })

    if not worker_routing_data:
        return issues

    # 批量查询联号库，取出 branch_name 和 institution_name
    all_routings = {w['routing'] for w in worker_routing_data}
    routing_db_map: Dict[str, Dict[str, str]] = {}  # routing -> {branch_name, institution_name}

    routing_list = list(all_routings)
    chunk_size = 500
    for i in range(0, len(routing_list), chunk_size):
        chunk = routing_list[i:i + chunk_size]
        results = bank_db.query(
            BankRouting.routing_number,
            BankRouting.branch_name,
            BankRouting.institution_name,
        ).filter(BankRouting.routing_number.in_(chunk)).all()
        for row in results:
            routing_db_map[row[0]] = {
                'branch_name':    row[1] or '',
                'institution_name': row[2] or '',
            }

    # 逐个工人校验
    for w in worker_routing_data:
        routing = w['routing']
        name = w['name']
        id_card = w['id_card']

        if routing not in routing_db_map:
            issues.append({
                'severity': 'error',
                'issue_type': 'bank_routing',
                'worker_name': name,
                'id_card': id_card,
                'field': '联行号',
                'description': f'联行号 {routing} 在联号库中找不到对应记录',
                'source_a': f'联行号: {routing}',
                'source_b': f'提交银行名称: {w["bank_name"] or "未填写"}',
            })
            continue

        db_info = routing_db_map[routing]

        # 校验 开户行名称 vs branch_name
        if w['bank_name'] and w['bank_name'] != db_info['branch_name']:
            issues.append({
                'severity': 'error',
                'issue_type': 'bank_routing',
                'worker_name': name,
                'id_card': id_card,
                'field': '开户行名称',
                'description': '开户行名称与联号库不一致',
                'source_a': f'提交填写: {w["bank_name"]}',
                'source_b': f'联号库标准名称: {db_info["branch_name"]}',
            })

        # 校验 收款方银行名称 vs institution_name（仅当支付明细表提供了该字段）
        if w['bank_inst_name'] and w['bank_inst_name'] != db_info['institution_name']:
            issues.append({
                'severity': 'error',
                'issue_type': 'bank_routing',
                'worker_name': name,
                'id_card': id_card,
                'field': '收款方银行名称',
                'description': '收款方银行名称（机构全称）与联号库不一致',
                'source_a': f'支付明细填写: {w["bank_inst_name"]}',
                'source_b': f'联号库机构全称: {db_info["institution_name"]}',
            })

    return issues


# ==================== 第三层：BIN码核查 ====================

# 银行卡BIN前缀映射（6位前缀 -> 银行简称关键词）
# 从长到短排列，优先匹配最具体的前缀
_BIN_BANK_KEYWORDS: List[Tuple[str, str]] = [
    # 农业银行（最具区分性的前缀放最前）
    ('9559',   '农业银行'),
    ('622841', '农业银行'), ('622848', '农业银行'), ('622849', '农业银行'),
    ('623018', '农业银行'), ('623019', '农业银行'),
    # 工商银行
    ('622202', '工商银行'), ('622203', '工商银行'), ('622208', '工商银行'),
    ('622209', '工商银行'), ('621226', '工商银行'), ('621227', '工商银行'),
    ('621228', '工商银行'),
    # 建设银行
    ('621700', '建设银行'), ('436742', '建设银行'), ('621799', '建设银行'),
    ('621284', '建设银行'), ('625362', '建设银行'), ('625975', '建设银行'),
    # 中国银行
    ('621660', '中国银行'), ('621661', '中国银行'), ('621662', '中国银行'),
    ('621663', '中国银行'), ('622760', '中国银行'), ('622761', '中国银行'),
    # 邮政储蓄银行
    ('621096', '邮政储蓄'), ('621098', '邮政储蓄'), ('621099', '邮政储蓄'),
    ('622150', '邮政储蓄'), ('622151', '邮政储蓄'), ('622188', '邮政储蓄'),
    ('621218', '邮政储蓄'), ('623218', '邮政储蓄'),
    # 交通银行
    ('622260', '交通银行'), ('622261', '交通银行'), ('622262', '交通银行'),
    ('622263', '交通银行'), ('622264', '交通银行'), ('622265', '交通银行'),
    # 招商银行
    ('622580', '招商银行'), ('622588', '招商银行'), ('621483', '招商银行'),
    ('625383', '招商银行'),
]

def _get_bin_bank_keyword(card_number: str) -> Optional[str]:
    """根据银行卡号前缀返回银行简称关键词，无法识别返回None"""
    for prefix, keyword in _BIN_BANK_KEYWORDS:
        if card_number.startswith(prefix):
            return keyword
    return None


def check_bin_codes(
    all_id_cards: set,
    registry: Dict[str, Dict],
    salary: Dict[str, Dict],
    payment: Dict[str, Dict],
) -> List[Dict[str, Any]]:
    """
    BIN码核查：银行卡号前缀所对应银行，必须与开户行名称中的银行一致。
    例如：卡号BIN识别为农业银行，但开户行填写了建设银行，即为错误。
    注：BIN库不完整，无法识别的卡号不予报错（跳过）。
    """
    issues = []

    for id_card in all_id_cards:
        r = registry.get(id_card, {})
        s = salary.get(id_card, {})
        p = payment.get(id_card, {})

        name = r.get('name') or s.get('name') or p.get('name') or '未知'

        # 银行卡号（优先取实名制表，其次工资表，再次支付明细）
        bank_card = r.get('bank_card') or s.get('bank_card') or p.get('bank_card') or ''
        if not bank_card:
            continue

        card_str = str(bank_card).replace(' ', '')
        bin_keyword = _get_bin_bank_keyword(card_str)
        if not bin_keyword:
            continue  # 无法识别BIN，跳过

        # 收集所有表中的开户行名称进行比对
        bank_names = {}
        for src, data in [('实名制表', r), ('工资表', s), ('支付明细', p)]:
            bn = data.get('bank_name') or data.get('bank_inst_name') or ''
            if bn:
                bank_names[src] = bn

        for src, bn in bank_names.items():
            if bin_keyword not in bn:
                issues.append({
                    'severity': 'error',
                    'issue_type': 'bin_mismatch',
                    'worker_name': name,
                    'id_card': id_card,
                    'field': '银行卡/开户行',
                    'description': (
                        f'银行卡号BIN识别为{bin_keyword}，'
                        f'但{src}中开户行填写为"{bn}"，两者不符'
                    ),
                    'source_a': f'卡号BIN({card_str[:6]}...): {bin_keyword}',
                    'source_b': f'{src}开户行: {bn}',
                })

    return issues


# ==================== 第四层：工资金额逻辑核查 ====================

def check_salary_logic(
    all_id_cards: set,
    salary: Dict[str, Dict],
    payment: Dict[str, Dict],
) -> List[Dict[str, Any]]:
    """
    工资金额逻辑核查：
    1. 应发工资 - 代扣/代缴 = 实发工资（工资表内部一致性）
    2. 支付明细表金额 = 实发工资（跨表一致性）
    允许±1元的四舍五入误差。
    """
    issues = []
    TOLERANCE = 1.0  # 允许误差1元

    for id_card in all_id_cards:
        s = salary.get(id_card, {})
        p = payment.get(id_card, {})

        name = s.get('name') or p.get('name') or '未知'

        # 从工资表取金额字段
        gross   = s.get('gross_salary')   # 应发工资
        deduct  = s.get('deduction', 0)   # 代扣/代缴（可为0）
        net_sal = s.get('net_salary')     # 实发工资

        # 从支付明细取实付金额
        paid    = p.get('amount')         # 支付金额

        try:
            gross   = float(gross)   if gross   is not None else None
            deduct  = float(deduct)  if deduct  is not None else 0.0
            net_sal = float(net_sal) if net_sal is not None else None
            paid    = float(paid)    if paid    is not None else None
        except (TypeError, ValueError):
            continue

        # 检查1：应发 - 代扣 = 实发
        if gross is not None and net_sal is not None:
            expected_net = gross - deduct
            if abs(expected_net - net_sal) > TOLERANCE:
                issues.append({
                    'severity': 'error',
                    'issue_type': 'salary_logic',
                    'worker_name': name,
                    'id_card': id_card,
                    'field': '工资金额',
                    'description': (
                        f'应发({gross}) - 代扣({deduct}) = {expected_net:.2f}，'
                        f'但实发工资填写为 {net_sal}，差额 {abs(expected_net - net_sal):.2f} 元'
                    ),
                    'source_a': f'工资表: 应发{gross} - 代扣{deduct} = {expected_net:.2f}',
                    'source_b': f'工资表实发: {net_sal}',
                })

        # 检查2：支付明细金额 = 实发工资
        if paid is not None and net_sal is not None:
            if abs(paid - net_sal) > TOLERANCE:
                issues.append({
                    'severity': 'error',
                    'issue_type': 'salary_logic',
                    'worker_name': name,
                    'id_card': id_card,
                    'field': '支付金额',
                    'description': (
                        f'支付明细金额({paid})与工资表实发工资({net_sal})不一致，'
                        f'差额 {abs(paid - net_sal):.2f} 元'
                    ),
                    'source_a': f'支付明细: {paid}',
                    'source_b': f'工资表实发: {net_sal}',
                })

    return issues


# ==================== 第五层：出勤天数一致性核查 ====================

def check_attendance_consistency(
    all_id_cards: set,
    salary: Dict[str, Dict],
    attendance_by_name: Dict[str, Dict],
) -> List[Dict[str, Any]]:
    """
    出勤天数核查：考勤表出勤工日 必须与 工资表出勤天数 一致。
    考勤表以姓名为key，工资表以身份证为key。
    """
    issues = []

    for id_card in all_id_cards:
        s = salary.get(id_card, {})
        name = s.get('name', '')
        if not name:
            continue

        att = attendance_by_name.get(name, {})
        if not att:
            continue  # 人员不在考勤表中，已由 check_cross_tables 报告

        days_salary = s.get('days_attended')     # 工资表出勤天数
        days_att    = att.get('days_attended')    # 考勤表出勤工日

        try:
            days_salary = float(days_salary) if days_salary is not None else None
            days_att    = float(days_att)    if days_att    is not None else None
        except (TypeError, ValueError):
            continue

        if days_salary is None or days_att is None:
            continue

        if days_salary != days_att:
            issues.append({
                'severity': 'error',
                'issue_type': 'attendance',
                'worker_name': name,
                'id_card': id_card,
                'field': '出勤天数',
                'description': (
                    f'考勤表出勤工日({days_att})与工资表出勤天数({days_salary})不一致'
                ),
                'source_a': f'考勤表: {days_att}天',
                'source_b': f'工资表: {days_salary}天',
            })

    return issues


# ==================== 第六层：站班会核查 ====================

def check_station_meeting(
    attendance_by_name: Dict[str, Dict],
    station_meeting: Dict,
) -> List[Dict[str, Any]]:
    """
    站班会核查：以考勤表为基准，逐人比对站班会中的出勤次数。
    - 考勤天数 == 站班会统计天数 → 正常
    - 考勤天数 != 站班会统计天数 → 错误
    - 站班会中找不到此人 → 警告（姓名对不上或考勤有误）
    - 同一人同一天在站班会中出现多次 → 警告（站班会数据异常）
    """
    issues = []
    sm_attendance: Dict[str, int] = station_meeting.get('attendance_by_name', {})
    sm_duplicates: Dict[str, list] = station_meeting.get('duplicate_days', {})

    # 先报站班会自身重复的问题
    for name, dup_dates in sm_duplicates.items():
        issues.append({
            'severity': 'warning',
            'issue_type': 'station_meeting',
            'worker_name': name,
            'id_card': '',
            'field': '站班会数据',
            'description': f'站班会中"{name}"在以下日期重复出现，数据可能有误：{", ".join(dup_dates)}',
            'source_a': '站班会: 重复记录',
            'source_b': None,
        })

    # 逐人核查
    for name, att_info in attendance_by_name.items():
        days_att = att_info.get('days_attended')
        try:
            days_att = float(days_att) if days_att is not None else None
        except (TypeError, ValueError):
            days_att = None

        if days_att is None:
            continue

        if name not in sm_attendance:
            issues.append({
                'severity': 'warning',
                'issue_type': 'station_meeting',
                'worker_name': name,
                'id_card': '',
                'field': '出勤天数',
                'description': f'考勤表记录"{name}"出勤{days_att}天，但在站班会中未找到此人，请核实姓名是否填写有误',
                'source_a': f'考勤表: {days_att}天',
                'source_b': '站班会: 未找到',
            })
            continue

        days_sm = float(sm_attendance[name])
        if days_att != days_sm:
            issues.append({
                'severity': 'error',
                'issue_type': 'station_meeting',
                'worker_name': name,
                'id_card': '',
                'field': '出勤天数',
                'description': f'考勤表出勤天数({int(days_att)}天)与站班会统计天数({int(days_sm)}天)不一致',
                'source_a': f'考勤表: {int(days_att)}天',
                'source_b': f'站班会: {int(days_sm)}天',
            })

    return issues


# ==================== 第七层：用工协议核查 ====================

def check_contracts(
    all_id_cards: set,
    salary: Dict[str, Dict],
    attendance_by_name: Dict[str, Dict],
    db: Session,
) -> List[Dict[str, Any]]:
    """
    用工协议核查：
    1. 无用工协议 → 警告
    2. 日工资 × 考勤天数 ≠ 应发工资 → 错误
    3. 有代扣项（代扣>0）→ 提示
    """
    from models import WorkerContract

    issues = []

    for id_card in all_id_cards:
        s = salary.get(id_card, {})
        name = s.get('name', '') or id_card

        contracts = db.query(WorkerContract).filter(
            WorkerContract.id_card == id_card
        ).all()

        if not contracts:
            issues.append({
                'severity': 'warning',
                'issue_type': 'contract',
                'worker_name': name,
                'id_card': id_card,
                'field': '用工协议',
                'description': '该工人无用工协议记录，请及时补签并上传',
                'source_a': None,
                'source_b': None,
            })
            continue

        # 取日工资（优先取有效协议的第一份）
        contract = contracts[0]
        daily_wage = contract.daily_wage

        if daily_wage is None:
            continue

        # 获取考勤天数（优先工资表，其次考勤表）
        days = s.get('days_attended')
        if days is None:
            att = attendance_by_name.get(name, {})
            days = att.get('days_attended')

        try:
            days = float(days) if days is not None else None
        except (TypeError, ValueError):
            days = None

        if days is None:
            continue

        # 检查代扣
        deduction = s.get('deduction')
        try:
            deduction = float(deduction) if deduction is not None else 0.0
        except (TypeError, ValueError):
            deduction = 0.0

        if deduction > 0:
            issues.append({
                'severity': 'info',
                'issue_type': 'contract',
                'worker_name': name,
                'id_card': id_card,
                'field': '代扣项',
                'description': f'该工人有代扣项 {deduction} 元，应发工资已扣除，请人工核实是否合理',
                'source_a': f'代扣: {deduction}元',
                'source_b': None,
            })

        # 日工资 × 考勤天数 = 应发工资
        expected = round(daily_wage * days, 2)
        gross = s.get('gross_salary')
        try:
            gross = float(gross) if gross is not None else None
        except (TypeError, ValueError):
            gross = None

        if gross is not None and abs(expected - gross) > 0.5:
            issues.append({
                'severity': 'error',
                'issue_type': 'contract',
                'worker_name': name,
                'id_card': id_card,
                'field': '应发工资',
                'description': (
                    f'按协议日工资{daily_wage}元×出勤{int(days)}天='
                    f'{expected}元，与工资表应发{gross}元不符'
                ),
                'source_a': f'协议计算: {expected}元',
                'source_b': f'工资表: {gross}元',
            })

    return issues


# ==================== 第八层：历史数据核对 ====================

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
