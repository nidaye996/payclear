"""
数据库模型定义
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    Text, Float, Boolean, Index
)
from sqlalchemy.orm import relationship
from database import Base, BankBase
# 注意：WorkerBankInfo 的 status 字段通过 lifespan 中的 ALTER TABLE 向后兼容添加


class Team(Base):
    """分包队伍"""
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, comment="队伍名称")
    contact_person = Column(String(50), comment="联系人")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    users = relationship("User", back_populates="team")
    submissions = relationship("MonthlySubmission", back_populates="team")
    worker_bank_infos = relationship("WorkerBankInfo", back_populates="team")


class User(Base):
    """用户"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, unique=True, comment="用户名")
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="team_leader", comment="admin/team_leader")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, comment="所属队伍(admin无需)")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # 关联
    team = relationship("Team", back_populates="users")


class Worker(Base):
    """工人基本信息"""
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, comment="姓名")
    id_card = Column(String(18), nullable=False, unique=True, comment="身份证号")
    phone = Column(String(11), comment="手机号")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联
    bank_infos = relationship("WorkerBankInfo", back_populates="worker")

    __table_args__ = (
        Index("ix_workers_id_card", "id_card"),
    )


class WorkerBankInfo(Base):
    """工人银行信息（支持历史变更）"""
    __tablename__ = "worker_bank_info"

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    bank_card = Column(String(25), comment="银行卡号")
    bank_name = Column(String(100), comment="开户银行名称")
    bank_branch = Column(String(200), comment="开户行支行")
    routing_number = Column(String(12), comment="联行号")
    valid_from = Column(String(7), comment="生效月份 YYYY-MM")
    valid_to = Column(String(7), nullable=True, comment="失效月份 YYYY-MM，NULL表示当前有效")
    created_at = Column(DateTime, default=datetime.utcnow)
    # status: 'pending'=暂存库, 'confirmed'=正式库
    # 该字段通过 lifespan 的 ALTER TABLE 向后兼容添加，历史数据默认 confirmed
    status = Column(String(20), default='pending', comment="pending/confirmed")

    # 关联
    worker = relationship("Worker", back_populates="bank_infos")
    team = relationship("Team", back_populates="worker_bank_infos")
    revocation_logs = relationship("RevocationLog", back_populates="bank_info")

    __table_args__ = (
        Index("ix_worker_bank_worker_id", "worker_id"),
        Index("ix_worker_bank_routing", "routing_number"),
        Index("ix_worker_bank_card", "bank_card"),
    )


class RevocationLog(Base):
    """回撤记录表"""
    __tablename__ = "revocation_logs"

    id = Column(Integer, primary_key=True)
    worker_id = Column(Integer, ForeignKey("workers.id"))
    bank_info_id = Column(Integer, ForeignKey("worker_bank_info.id"))
    worker_name = Column(String(50), comment="冗余存储，方便查询")
    id_card = Column(String(18))
    previous_status = Column(String(20), comment="回撤前的状态")
    revoked_by = Column(Integer, ForeignKey("users.id"), comment="操作人")
    revoked_at = Column(DateTime, default=datetime.utcnow)
    reason = Column(String(500), nullable=True, comment="回撤原因（可选）")
    bank_card = Column(String(25), comment="记录当时的银行卡号")
    bank_name = Column(String(100))
    routing_number = Column(String(12))

    # 关联
    worker = relationship("Worker")
    bank_info = relationship("WorkerBankInfo", back_populates="revocation_logs")
    operator = relationship("User")


class PaymentReceipt(Base):
    """打款回执记录表"""
    __tablename__ = "payment_receipts"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("monthly_submissions.id"), nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    total_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    receipt_data = Column(Text, comment="JSON，存解析后的明细")

    # 关联
    team = relationship("Team")
    uploader = relationship("User")


class DeletedWorkerArchive(Base):
    """已删除工人备份表"""
    __tablename__ = "deleted_worker_archive"

    id = Column(Integer, primary_key=True)
    original_worker_id = Column(Integer, comment="原worker.id")
    name = Column(String(50))
    id_card = Column(String(18))
    phone = Column(String(11))
    bank_card = Column(String(25))
    bank_name = Column(String(100))
    bank_branch = Column(String(200))
    routing_number = Column(String(12))
    team_id = Column(Integer)
    team_name = Column(String(100))
    status = Column(String(20), comment="删除前的状态")
    deleted_by = Column(Integer, ForeignKey("users.id"))
    deleted_at = Column(DateTime, default=datetime.utcnow)
    delete_reason = Column(String(500), nullable=True)

    operator = relationship("User")


class MonthlySubmission(Base):
    """月度提交记录"""
    __tablename__ = "monthly_submissions"

    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    year = Column(Integer, nullable=False, comment="年份")
    month = Column(Integer, nullable=False, comment="月份")
    submitted_at = Column(DateTime, default=datetime.utcnow)
    submitted_by = Column(Integer, ForeignKey("users.id"), comment="提交人")
    status = Column(String(20), default="pending", comment="pending/checking/done/error")
    is_historical = Column(Boolean, default=False, comment="是否历史数据导入")

    # 关联
    team = relationship("Team", back_populates="submissions")
    files = relationship("SubmissionFile", back_populates="submission", cascade="all, delete-orphan")
    reports = relationship("CheckReport", back_populates="submission", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_submissions_team_year_month", "team_id", "year", "month"),
    )


class SubmissionFile(Base):
    """提交的文件"""
    __tablename__ = "submission_files"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("monthly_submissions.id"), nullable=False)
    file_type = Column(String(30), nullable=False, comment="实名制表/工资表/支付明细")
    original_filename = Column(String(255), comment="原始文件名")
    file_path = Column(String(500), comment="存储路径")
    parsed_data = Column(Text, comment="解析后的JSON数据")
    parse_status = Column(String(20), default="pending", comment="解析状态")
    parse_error = Column(Text, comment="解析错误信息")
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    submission = relationship("MonthlySubmission", back_populates="files")


class CheckReport(Base):
    """核对报告"""
    __tablename__ = "check_reports"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, ForeignKey("monthly_submissions.id"), nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow)
    report_data = Column(Text, comment="报告JSON数据")
    total_workers = Column(Integer, default=0, comment="总工人数")
    error_count = Column(Integer, default=0, comment="错误数量")
    warning_count = Column(Integer, default=0, comment="警告数量")

    # 关联
    submission = relationship("MonthlySubmission", back_populates="reports")


# ============== 银行联号库模型（独立数据库）==============

class BankRouting(BankBase):
    """银行联号库"""
    __tablename__ = "bank_routing"

    id = Column(Integer, primary_key=True, index=True)
    institution_name = Column(String(200), comment="银行机构全称")
    routing_number = Column(String(12), nullable=False, comment="联行号")
    branch_name = Column(String(200), comment="开户行名称")
    province = Column(String(50), comment="银行所属省名称")
    city = Column(String(50), comment="银行所属市名称")
    bank_type_code = Column(String(20), comment="行别代码")

    __table_args__ = (
        Index("ix_bank_routing_number", "routing_number"),
        Index("ix_bank_routing_branch_name", "branch_name"),
    )
