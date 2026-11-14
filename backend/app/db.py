from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

_kw = {"connect_args": {"check_same_thread": False}} if config.DATABASE_URL.startswith("sqlite") else {"pool_pre_ping": True}
engine = create_engine(config.DATABASE_URL, **_kw)
if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _fk_on(conn, _):          # SQLite ignores ON DELETE CASCADE unless asked
        conn.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models, buckets  # noqa: F401
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        buckets.seed_defaults(db)
