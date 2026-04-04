"""
队伍管理路由
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Team, User
from schemas import TeamCreate, TeamOut
from routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/teams", tags=["队伍管理"])


@router.get("", response_model=List[TeamOut])
def list_teams(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取队伍列表（管理员/操作员看全部，队长看自己）"""
    if current_user.role in ("admin", "operator"):
        return db.query(Team).all()
    else:
        if current_user.team_id:
            team = db.query(Team).filter(Team.id == current_user.team_id).first()
            return [team] if team else []
        return []


@router.post("", response_model=TeamOut)
def create_team(
    team_data: TeamCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """创建队伍（管理员）"""
    existing = db.query(Team).filter(Team.name == team_data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="队伍名称已存在")

    team = Team(name=team_data.name, contact_person=team_data.contact_person)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@router.get("/{team_id}", response_model=TeamOut)
def get_team(
    team_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取队伍详情"""
    if current_user.role != "admin" and current_user.team_id != team_id:
        raise HTTPException(status_code=403, detail="无权访问此队伍")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    return team


@router.put("/{team_id}", response_model=TeamOut)
def update_team(
    team_id: int,
    team_data: TeamCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """修改队伍信息（管理员）"""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")

    team.name = team_data.name
    team.contact_person = team_data.contact_person
    db.commit()
    db.refresh(team)
    return team


@router.delete("/{team_id}")
def delete_team(
    team_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """删除队伍（管理员）"""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")

    # 检查是否有关联数据
    if team.submissions:
        raise HTTPException(status_code=400, detail="该队伍有提交记录，无法删除")

    db.delete(team)
    db.commit()
    return {"message": "队伍已删除"}
