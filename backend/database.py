"""
数据库配置模块
使用 SQLAlchemy ORM，SQLite 数据库
"""
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# 数据目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 主数据库（工人、提交记录、报告等）
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'salary.db')}"

# 银行联号库（单独存储，方便替换）
BANK_DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'bank_routing.db')}"

# 主数据库引擎
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# 银行联号库引擎
bank_engine = create_engine(
    BANK_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# 启用 WAL 模式，提升并发性能
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

@event.listens_for(bank_engine, "connect")
def set_bank_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

# Session 工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
BankSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=bank_engine)

# ORM 基类
Base = declarative_base()
BankBase = declarative_base()


def get_db():
    """依赖注入：获取主数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_bank_db():
    """依赖注入：获取银行联号库会话"""
    db = BankSessionLocal()
    try:
        yield db
    finally:
        db.close()
