from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def apply_sqlite_write_serialization(sqla_engine) -> None:
    """让 SQLite 引擎的每个写事务一上来就 BEGIN IMMEDIATE（见下）。
    抽成函数：生产引擎在模块加载时装好，测试自建引擎也可复用同一套保证。"""
    if not sqla_engine.url.drivername.startswith("sqlite"):
        return

    @event.listens_for(sqla_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA busy_timeout=10000")  # 等锁最多 10s，别立刻抛 busy
        cursor.close()
        # 关掉 pysqlite 自己的隐式 BEGIN，由下面的 begin 事件显式发 IMMEDIATE
        dbapi_conn.isolation_level = None

    @event.listens_for(sqla_engine, "begin")
    def _sqlite_begin_immediate(conn):
        # SQLite 方言里 SELECT ... FOR UPDATE 是空操作。默认 DEFERRED 事务下，
        # 订单页与备料台几乎同时点「生成」时两边都先读、后写，各起一套 run，
        # 直到提交才在唯一承诺约束上撞车——仍可能有一边先落了半空的废账再回退。
        # BEGIN IMMEDIATE 一进来就拿 RESERVED 写锁：写请求在入口排队，
        # 先到者落唯一一套两本账，后到者开事务后直接读到旧账，原样返回同一套；
        # 同时避免 DEFERRED 事务升级写锁时两边互等的死锁。
        conn.exec_driver_sql("BEGIN IMMEDIATE")


apply_sqlite_write_serialization(engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
