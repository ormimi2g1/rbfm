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


def _add_missing_columns():
    """create_all() never alters an existing table; add columns introduced later."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have and col.nullable and not col.primary_key:
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                with engine.begin() as conn:
                    conn.execute(text(ddl))


def init_db():
    from . import models, buckets  # noqa: F401
    Base.metadata.create_all(engine)
    _add_missing_columns()
    with SessionLocal() as db:
        buckets.seed_defaults(db)
