import os
from dotenv import load_dotenv
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

load_dotenv()

DATABASE_URL = (
    os.getenv("DATABASE_URL_POOLED")
    or os.getenv("DATABASE_URL")
    or "sqlite:///./workout.db"
)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(
    DATABASE_URL, echo=False, connect_args=connect_args, pool_pre_ping=True
)


def get_session():
    with Session(engine) as session:
        yield session


def run_safe_migrations():
    """Ensure split and macro-target columns exist on existing SQLite/PostgreSQL user tables."""
    with engine.begin() as conn:
        if "sqlite" in DATABASE_URL:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(user)")).fetchall()]
            if "split_template_ids" not in cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN split_template_ids TEXT DEFAULT '[]'"))
            if "split_index" not in cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN split_index INTEGER DEFAULT 0"))
            if "target_calories" not in cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN target_calories INTEGER"))
            if "target_protein" not in cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN target_protein FLOAT"))
            if "target_carbs" not in cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN target_carbs FLOAT"))
            if "target_fats" not in cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN target_fats FLOAT"))
        else:
            conn.execute(
                text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS split_template_ids JSON DEFAULT \'[]\'::json')
            )
            conn.execute(
                text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS split_index INTEGER DEFAULT 0')
            )
            conn.execute(
                text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS target_calories INTEGER')
            )
            conn.execute(
                text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS target_protein DOUBLE PRECISION')
            )
            conn.execute(
                text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS target_carbs DOUBLE PRECISION')
            )
            conn.execute(
                text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS target_fats DOUBLE PRECISION')
            )


def init_db():
    SQLModel.metadata.create_all(engine)
    run_safe_migrations()