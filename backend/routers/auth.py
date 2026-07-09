"""
认证路由：登录、获取当前用户信息、用户管理
"""
from datetime import datetime, timedelta
from typing import Optional, List
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import get_db
from models import User
from schemas import TokenResponse, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["认证"])

import logging
_audit = logging.getLogger("audit")

# JWT 配置
import os
from security import require_strong_password

_DEV_SECRET = "dev-only-secret-change-in-production"
SECRET_KEY = os.environ.get("SECRET_KEY")
PAYCLEAR_ENV = os.environ.get("PAYCLEAR_ENV", "production").lower()
if not SECRET_KEY:
    if PAYCLEAR_ENV in {"dev", "development", "local"}:
        SECRET_KEY = _DEV_SECRET
    else:
        raise RuntimeError("生产环境必须设置 SECRET_KEY")
elif SECRET_KEY == _DEV_SECRET and PAYCLEAR_ENV not in {"dev", "development", "local"}:
    raise RuntimeError("生产环境不能使用默认 SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8小时

# 密码加密
import bcrypt as _bcrypt
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """依赖注入：从 JWT Token 获取当前用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if user is None:
        raise credentials_exception
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """依赖注入：要求管理员权限"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


def require_admin_or_operator(current_user: User = Depends(get_current_user)) -> User:
    """依赖注入：要求管理员或操作员权限"""
    if current_user.role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="需要管理员或操作员权限")
    return current_user


# ==================== 登录限速 ====================

# { ip: {"count": N, "locked_until": datetime or None} }
_login_attempts: dict = defaultdict(lambda: {"count": 0, "locked_until": None})
MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    trust_proxy = os.environ.get("TRUST_PROXY_HEADERS", "").lower() in {"1", "true", "yes"}
    if trust_proxy and forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str):
    entry = _login_attempts[ip]
    now = datetime.utcnow()
    if entry["locked_until"] and now < entry["locked_until"]:
        remaining = int((entry["locked_until"] - now).total_seconds() / 60) + 1
        _audit.warning("LOGIN_BLOCKED ip=%s remaining_min=%d", ip, remaining)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"登录失败次数过多，请 {remaining} 分钟后再试",
        )


def _record_failure(ip: str, username: str):
    entry = _login_attempts[ip]
    entry["count"] += 1
    if entry["count"] >= MAX_ATTEMPTS:
        entry["locked_until"] = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        entry["count"] = 0
        _audit.warning("LOGIN_LOCKED ip=%s username=%s locked_min=%d", ip, username, LOCKOUT_MINUTES)
    else:
        _audit.warning("LOGIN_FAIL ip=%s username=%s attempt=%d", ip, username, entry["count"])


def _record_success(ip: str):
    _login_attempts[ip] = {"count": 0, "locked_until": None}


# ==================== 路由 ====================

@router.post("/login", response_model=TokenResponse)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """用户登录"""
    ip = _get_client_ip(request)
    _check_rate_limit(ip)

    user = db.query(User).filter(
        User.username == form_data.username,
        User.is_active == True
    ).first()

    if not user or not verify_password(form_data.password, user.password_hash):
        _record_failure(ip, form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    _record_success(ip)
    _audit.info("LOGIN_OK username=%s ip=%s role=%s", user.username, ip, user.role)
    token = create_access_token({"sub": str(user.id)})

    return TokenResponse(
        access_token=token,
        role=user.role,
        team_id=user.team_id,
        username=user.username,
        user_id=user.id,
    )


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return current_user


@router.get("/users", response_model=List[UserOut])
def list_users(
    current_user: User = Depends(require_admin_or_operator),
    db: Session = Depends(get_db)
):
    """获取用户列表（管理员看全部；操作员看队伍负责人+自己）"""
    if current_user.role == "admin":
        return db.query(User).all()
    # 操作员：只看队伍负责人和自己
    from sqlalchemy import or_
    return db.query(User).filter(
        or_(User.role == "team_leader", User.id == current_user.id)
    ).all()


@router.post("/users", response_model=UserOut)
def create_user(
    user_data: UserCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """创建用户（管理员）"""
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    if user_data.role not in ("admin", "operator", "team_leader"):
        raise HTTPException(status_code=400, detail="角色无效，只能是 admin、operator 或 team_leader")

    require_strong_password(user_data.password)

    new_user = User(
        username=user_data.username,
        password_hash=get_password_hash(user_data.password),
        role=user_data.role,
        team_id=user_data.team_id,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    _audit.info("USER_CREATE username=%s role=%s by=%s", new_user.username, new_user.role, current_user.username)
    return new_user


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """修改用户账号/密码（权限分级）"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 权限校验
    if current_user.role == "admin":
        pass  # 可修改任何人
    elif current_user.role == "operator":
        if current_user.id != user_id and target.role != "team_leader":
            raise HTTPException(status_code=403, detail="无权修改该用户")
    else:  # team_leader
        if current_user.id != user_id:
            raise HTTPException(status_code=403, detail="只能修改自己的账号")

    if not data.username and not data.password:
        raise HTTPException(status_code=400, detail="请提供新用户名或新密码")

    if data.username:
        existing = db.query(User).filter(
            User.username == data.username,
            User.id != user_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="用户名已存在")
        target.username = data.username

    if data.password:
        require_strong_password(data.password)
        target.password_hash = get_password_hash(data.password)

    db.commit()
    db.refresh(target)
    fields = []
    if data.username: fields.append("username")
    if data.password: fields.append("password")
    _audit.info("USER_UPDATE target=%s fields=%s by=%s", target.username, ",".join(fields), current_user.username)
    return {"message": "修改成功", "username": target.username}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """禁用用户（管理员）"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能禁用自己")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_active = False
    db.commit()
    _audit.info("USER_DISABLE target=%s by=%s", user.username, current_user.username)
    return {"message": "用户已禁用"}
