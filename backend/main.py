"""
FastAPI 主入口
工人工资核对系统
"""
import os
import json
import logging
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from database import Base, BankBase, engine, bank_engine, get_db, get_bank_db
import models  # 触发模型注册

from routers import auth, teams, workers, submissions, reports, historical, backup
from routers.auth import get_current_user, require_admin, require_admin_or_operator
from models import User, BankRouting

# 日志配置
import time
from logging.handlers import RotatingFileHandler

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
LOG_PATH = os.path.join(DATA_DIR, "app.log")

_log_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
_file_handler = RotatingFileHandler(
    LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=10, encoding='utf-8'
)
_file_handler.setFormatter(_log_formatter)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)

logging.root.setLevel(logging.INFO)
logging.root.addHandler(_file_handler)
logging.root.addHandler(_console_handler)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期管理"""
    # 创建所有数据库表
    Base.metadata.create_all(bind=engine)
    BankBase.metadata.create_all(bind=bank_engine)
    logger.info("数据库表创建完成")

    # 向后兼容：给已有的 worker_bank_info 表添加 status 列（若不存在）
    _migrate_db()

    # 初始化默认管理员账号
    _init_admin()

    yield

    logger.info("应用关闭")


def _migrate_db():
    """数据库迁移：向后兼容添加新字段"""
    with engine.connect() as conn:
        # 添加 status 列，历史数据默认 confirmed（已验证）
        try:
            conn.execute(
                __import__('sqlalchemy').text(
                    "ALTER TABLE worker_bank_info ADD COLUMN status VARCHAR DEFAULT 'confirmed'"
                )
            )
            conn.commit()
            logger.info("worker_bank_info.status 列添加成功")
        except Exception:
            pass  # 列已存在则忽略


def _init_admin():
    """初始化默认管理员账号"""
    from database import SessionLocal
    from routers.auth import get_password_hash

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "admin").first()
        if not existing:
            admin = User(
                username="admin",
                password_hash=get_password_hash("admin123"),
                role="admin",
            )
            db.add(admin)
            db.commit()
            logger.info("默认管理员账号创建成功: admin / admin123")
        else:
            logger.info("管理员账号已存在")
    except Exception as e:
        logger.error(f"初始化管理员失败: {e}")
        db.rollback()
    finally:
        db.close()


# ==================== FastAPI 应用 ====================

app = FastAPI(
    title="薪核通 PayClear",
    description="工程项目农民工工资核对管理系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置（开发环境允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 请求日志中间件 ====================

_req_logger = logging.getLogger("request")
_SKIP_LOG_PATHS = {"/api/bank-routing/stats", "/api/auth/me"}

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = int((time.time() - start) * 1000)
    path = request.url.path
    if path not in _SKIP_LOG_PATHS and not path.startswith("/static"):
        _req_logger.info(
            "%s %s %d %dms",
            request.method, path, response.status_code, duration_ms
        )
    return response


# ==================== 注册路由 ====================
app.include_router(auth.router, prefix="/api")
app.include_router(teams.router, prefix="/api")
app.include_router(workers.router, prefix="/api")
app.include_router(submissions.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(historical.router, prefix="/api")
app.include_router(backup.router, prefix="/api")


# ==================== 银行联号库管理路由 ====================

@app.post("/api/bank-routing/import")
async def import_bank_routing(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin_or_operator),
    db: Session = Depends(get_db),
    bank_db: Session = Depends(get_bank_db),
):
    """导入银行联号库（管理员或操作员，上传Excel文件）；导入后自动比对所有工人银行信息"""
    import tempfile
    from services.importer import import_bank_routing_file

    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in ('.xlsx', '.xls'):
        raise HTTPException(status_code=400, detail="请上传 Excel 文件 (.xlsx/.xls)")

    # 临时保存文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = import_bank_routing_file(tmp_path, bank_db)
        # 导入成功后，自动比对所有工人银行信息
        try:
            check_result = _check_workers_against_routing(db, bank_db)
            result["worker_check"] = check_result
        except Exception as ce:
            logger.warning(f"自动比对工人银行信息失败（不影响导入结果）: {ce}")
        return result
    except Exception as e:
        logger.error(f"导入银行联号库失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")
    finally:
        os.unlink(tmp_path)


@app.get("/api/bank-routing/stats")
def get_bank_routing_stats(
    current_user: User = Depends(get_current_user),
    bank_db: Session = Depends(get_bank_db),
):
    """获取联号库统计信息"""
    total = bank_db.query(BankRouting).count()
    return {"total_records": total}


def _check_workers_against_routing(db: Session, bank_db: Session) -> dict:
    """
    比对所有当前有效工人银行信息与联号库。
    返回: { "total_checked": N, "issues": [...], "ok_count": M }
    """
    from models import WorkerBankInfo, Worker

    # 查询所有当前有效的工人银行信息（status=confirmed, valid_to=NULL）
    records = (
        db.query(WorkerBankInfo, Worker)
        .join(Worker, WorkerBankInfo.worker_id == Worker.id)
        .filter(
            WorkerBankInfo.status == 'confirmed',
            WorkerBankInfo.valid_to == None,  # noqa: E711
            WorkerBankInfo.routing_number != None,  # noqa: E711
            WorkerBankInfo.routing_number != '',
        )
        .all()
    )

    issues = []
    ok_count = 0

    for bank_info, worker in records:
        routing = bank_info.routing_number
        bank_match = bank_db.query(BankRouting).filter(
            BankRouting.routing_number == routing
        ).first()

        if not bank_match:
            issues.append({
                "worker_name": worker.name,
                "id_card": worker.id_card,
                "bank_card": bank_info.bank_card,
                "routing_number": routing,
                "bank_name": bank_info.bank_name,
                "issue_type": "routing_not_found",
                "issue_desc": f"联行号 {routing} 在联号库中不存在",
            })
            continue

        # 检查银行名称是否吻合（模糊匹配：互相包含即视为匹配）
        worker_name_str = (bank_info.bank_name or '').strip()
        lib_branch = (bank_match.branch_name or '').strip()
        lib_inst = (bank_match.institution_name or '').strip()

        if worker_name_str and lib_branch:
            matched = (
                worker_name_str in lib_branch
                or lib_branch in worker_name_str
                or worker_name_str in lib_inst
                or lib_inst in worker_name_str
            )
            if not matched:
                issues.append({
                    "worker_name": worker.name,
                    "id_card": worker.id_card,
                    "bank_card": bank_info.bank_card,
                    "routing_number": routing,
                    "bank_name": worker_name_str,
                    "issue_type": "bank_name_mismatch",
                    "issue_desc": f"银行名称不匹配：系统中为「{worker_name_str}」，联号库显示「{lib_branch}」",
                    "lib_branch_name": lib_branch,
                })
                continue

        ok_count += 1

    return {
        "total_checked": len(records),
        "ok_count": ok_count,
        "issue_count": len(issues),
        "issues": issues,
    }


@app.get("/api/bank-routing/check-workers")
def check_workers_routing(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    bank_db: Session = Depends(get_bank_db),
):
    """手动触发：比对所有工人银行信息与联号库"""
    return _check_workers_against_routing(db, bank_db)


@app.get("/api/bank-routing/search")
def search_bank_routing(
    q: str = Query(..., min_length=1, description="搜索联行号或银行名称"),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    bank_db: Session = Depends(get_bank_db),
):
    """搜索银行联号库"""
    query = bank_db.query(BankRouting)

    if q.isdigit():
        # 按联行号精确或模糊查询
        query = query.filter(BankRouting.routing_number.like(f"{q}%"))
    else:
        # 按名称模糊查询
        query = query.filter(
            (BankRouting.institution_name.contains(q)) |
            (BankRouting.branch_name.contains(q))
        )

    results = query.limit(limit).all()
    return {
        "total": query.count(),
        "items": [
            {
                "routing_number": r.routing_number,
                "institution_name": r.institution_name,
                "branch_name": r.branch_name,
                "province": r.province,
                "city": r.city,
            }
            for r in results
        ]
    }


# ==================== 静态文件 & 前端路由 ====================

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/{page_name}.html")
    def serve_page(page_name: str):
        page_path = os.path.join(FRONTEND_DIR, f"{page_name}.html")
        if os.path.exists(page_path):
            return FileResponse(page_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
        raise HTTPException(status_code=404, detail="页面不存在")


# ==================== 健康检查 ====================

@app.get("/api/health")
def health_check():
    return {"status": "ok", "system": "薪核通 PayClear"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[os.path.dirname(__file__)],
    )
