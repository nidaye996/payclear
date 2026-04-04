"""
认证路由：登录、获取当前用户信息、用户管理
"""
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import get_db
from models import User
from schemas import TokenResponse, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["认证"])

# JWT 配置
SECRET_KEY = "salary-system-secret-key-change-in-production-2024"
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


# ==================== 路由 ====================

@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """用户登录"""
    user = db.query(User).filter(
        User.username == form_data.username,
        User.is_active == True
    ).first()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    token = create_access_token({"sub": str(user.id)})

    return TokenResponse(
        access_token=token,
        role=user.role,
        team_id=user.team_id,
        username=user.username,
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

    new_user = User(
        username=user_data.username,
        password_hash=get_password_hash(user_data.password),
        role=user_data.role,
        team_id=user_data.team_id,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
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
        if len(data.password) < 6:
            raise HTTPException(status_code=400, detail="密码至少6位")
        target.password_hash = get_password_hash(data.password)

    db.commit()
    db.refresh(target)
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
    return {"message": "用户已禁用"}
