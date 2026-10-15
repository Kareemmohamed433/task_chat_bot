"""Database seed script for default employees."""

import logging
from sqlalchemy.orm import Session
from app.database import Base, SessionLocal, engine
from app.models import Employee, RoleEnum

logger = logging.getLogger(__name__)

INITIAL_EMPLOYEES = [
    {"user_id": 1, "name": "منيرة", "department": "الإدارة", "role": RoleEnum.MANAGER},
    {"user_id": 2, "name": "سارة", "department": "المشاريع", "role": RoleEnum.SUPERVISOR},
    {"user_id": 3, "name": "فهد", "department": "المشاريع", "role": RoleEnum.EMPLOYEE},
    {"user_id": 4, "name": "خالد", "department": "المشاريع", "role": RoleEnum.EMPLOYEE},
    {"user_id": 5, "name": "نورة", "department": "المالية", "role": RoleEnum.EMPLOYEE},
    {"user_id": 6, "name": "عبدالرحمن", "department": "المالية", "role": RoleEnum.SUPERVISOR},
]


def seed_employees(db: Session) -> int:
    """Seed the database with initial employees reproducibly without duplication."""
    seeded_count = 0
    try:
        for emp_data in INITIAL_EMPLOYEES:
            existing = db.query(Employee).filter(Employee.user_id == emp_data["user_id"]).first()
            if not existing:
                emp = Employee(
                    user_id=emp_data["user_id"],
                    name=emp_data["name"],
                    department=emp_data["department"],
                    role=emp_data["role"],
                )
                db.add(emp)
                seeded_count += 1
            else:
                # Update details if changed to maintain consistency
                existing.name = emp_data["name"]
                existing.department = emp_data["department"]
                existing.role = emp_data["role"]

        db.commit()
        logger.info("Successfully seeded %d new employees (%d total configured).", seeded_count, len(INITIAL_EMPLOYEES))
    except Exception as e:
        db.rollback()
        logger.error("Failed to seed employees: %s", e)
        raise

    return seeded_count


def migrate_schema(db_engine=engine) -> None:
    """Safely apply schema migrations to preserve existing database records."""
    from sqlalchemy import inspect, text
    inspector = inspect(db_engine)
    if "tasks" in inspector.get_table_names():
        columns = [col["name"] for col in inspector.get_columns("tasks")]
        if "idempotency_key" not in columns:
            logger.info("Migrating schema: adding idempotency_key column to tasks table...")
            with db_engine.connect() as conn:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN idempotency_key VARCHAR(128)"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_tasks_idempotency_key ON tasks (idempotency_key)"))
                conn.commit()
        if "is_archived" not in columns:
            logger.info("Migrating schema: adding is_archived column to tasks table...")
            with db_engine.connect() as conn:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_is_archived ON tasks (is_archived)"))
                conn.commit()


def init_db(db_engine=engine) -> None:
    """Initialize database tables, apply migrations, and seed records."""
    Base.metadata.create_all(bind=db_engine)
    migrate_schema(db_engine)
    with SessionLocal() as db:
        seed_employees(db)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database initialized, migrated, and seeded successfully.")
