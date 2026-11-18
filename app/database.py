import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = DATA_DIR / "uploads"
IMAGES_DIR = DATA_DIR / "images"
EXPORTS_DIR = DATA_DIR / "exports"
# Resized copies of product photographs, generated on first request and reused after. The
# originals are what a manufacturer publishes - up to 2000x1919 and 1.5MB - and the app shows
# them at 130px in a grid of them, so sending the original is two orders of magnitude more
# data than the page can use. Derived, never authored: safe to delete, rebuilt on demand.
THUMBS_DIR = DATA_DIR / "thumbs"
for d in (UPLOADS_DIR, IMAGES_DIR, EXPORTS_DIR, THUMBS_DIR):
    d.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "app.db"

# Unset (every local/dev setup, and the default in production until you deliberately opt in)
# means SQLite against data/app.db, exactly as before. Set DATABASE_URL to a Postgres DSN -
# postgresql+psycopg2://user:pass@host/dbname - to run against Postgres instead; see
# deployment.md section 3.2 for when that's worth it and migrate_to_postgres.py for moving
# existing data across. The rest of the app has no raw SQL or SQLite-specific queries (it's
# SQLAlchemy ORM throughout), so this one connection-setup branch is the entire difference.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

    # WAL (Write-Ahead Logging) lets readers proceed while a write is in progress instead of
    # blocking on every write, and busy_timeout makes a momentary write-write collision retry
    # for up to 5s instead of immediately raising "database is locked" - the concurrency floor
    # this app needs once more than one person can be using it at once (see deployment.md
    # section 3.2). SQLite-only: these PRAGMAs don't exist in Postgres, which handles
    # concurrent readers/writers natively via MVCC and needs neither. Applied per-connection
    # (SQLAlchemy's "connect" event) since a PRAGMA is a per-connection setting in SQLite, not
    # a database-wide one.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
else:
    # pool_pre_ping: checks a pooled connection is still alive before handing it to a request,
    # so a connection Postgres (or a firewall/load balancer in between) silently closed after
    # sitting idle doesn't surface as a confusing mid-request error on the next request to use it.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
