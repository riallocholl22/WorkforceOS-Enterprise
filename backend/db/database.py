import os

from dotenv import load_dotenv
from sqlalchemy import Column, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "sqlite:///./app.db"

engine_kwargs = {
    "pool_pre_ping": True,
}

if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    role = Column(String)
    content = Column(String)


def _ensure_column(table_name: str, column_name: str, ddl: str):
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in existing_columns:
        return

    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def init_db():
    from backend.db import models
    from backend.models import enterprise, job, report
    from backend.services import auth_service

    Base.metadata.create_all(bind=engine)

    _ensure_column("users", "organization_id", "INTEGER")
    _ensure_column("users", "role", "VARCHAR DEFAULT 'recruiter'")
    _ensure_column("users", "email_verified", "BOOLEAN DEFAULT 0")
    _ensure_column("users", "refresh_token_version", "INTEGER DEFAULT 0")
    _ensure_column("candidates", "organization_id", "INTEGER")
    _ensure_column("candidates", "raw_text", "TEXT")
    _ensure_column("candidates", "extraction_json", "TEXT")
    _ensure_column("candidates", "file_hash", "VARCHAR")
    _ensure_column("candidates", "candidate_name", "VARCHAR")
    _ensure_column("candidates", "name_confidence", "INTEGER")
    _ensure_column("candidates", "name_source", "VARCHAR")
    _ensure_column("candidates", "extracted_email", "VARCHAR")
    _ensure_column("candidates", "extracted_phone", "VARCHAR")
    _ensure_column("candidates", "updated_at", "DATETIME")
    _ensure_column("jobs", "organization_id", "INTEGER")
