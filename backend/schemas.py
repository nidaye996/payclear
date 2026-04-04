"""
Pydantic 数据验证模式
"""
from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, field_validator
import re


# ==================== 认证 ====================

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    team_id: Optional[int] = None
    username: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "team_leader"
    team_id: Optional[int] = None


class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    team_id: Optional[int] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== 队伍 ====================

class TeamCreate(BaseModel):
    name: str
    contact_person: Optional[str] = None


class TeamOut(BaseModel):
    id: int
    name: str
    contact_person: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ==================== 工人 ====================

class WorkerOut(BaseModel):
    id: int
    name: str
    id_card: str
    phone: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkerBankInfoOut(BaseModel):
    id: int
    worker_id: int
    team_id: int
    bank_card: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    routing_number: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    status: Optional[str] = 'confirmed'  # pending/confirmed

    model_config = {"from_attributes": True}


class WorkerDetailOut(BaseModel):
    id: int
    name: str
    id_card: str
    phone: Optional[str] = None
    created_at: datetime
    bank_infos: List[WorkerBankInfoOut] = []

    model_config = {"from_attributes": True}


# ==================== 月度提交 ====================

class SubmissionCreate(BaseModel):
    team_id: int
    year: int
    month: int
    is_historical: bool = False


class SubmissionFileOut(BaseModel):
    id: int
    file_type: str
    original_filename: Optional[str] = None
    parse_status: str
    parse_error: Optional[str] = None
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class SubmissionOut(BaseModel):
    id: int
    team_id: int
    year: int
    month: int
    submitted_at: datetime
    status: str
    is_historical: bool
    files: List[SubmissionFileOut] = []

    model_config = {"from_attributes": True}


# ==================== 报告 ====================

class ReportIssue(BaseModel):
    """单条问题记录"""
    severity: str          # error / warning / info
    issue_type: str        # cross_table / bank_routing / history
    worker_name: str
    id_card: str
    field: str             # 问题字段
    description: str       # 问题描述
    source_a: Optional[str] = None   # 来源A的值
    source_b: Optional[str] = None   # 来源B的值


class ReportOut(BaseModel):
    id: int
    submission_id: int
    generated_at: datetime
    total_workers: int
    error_count: int
    warning_count: int
    issues: List[Dict[str, Any]] = []

    model_config = {"from_attributes": True}


# ==================== 银行联号库 ====================

class BankRoutingOut(BaseModel):
    id: int
    institution_name: Optional[str] = None
    routing_number: str
    branch_name: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None

    model_config = {"from_attributes": True}


class BankRoutingSearchResult(BaseModel):
    total: int
    items: List[BankRoutingOut]


# ==================== 通用响应 ====================

class MessageResponse(BaseModel):
    message: str
    success: bool = True


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[Any]
