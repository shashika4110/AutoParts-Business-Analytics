"""Create the Phase 2 PostgreSQL business schema.

Revision ID: 0001_initial
Revises:
"""

from pathlib import Path
import re

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TABLES_REVERSE_ORDER = (
    "audit_logs",
    "expenses",
    "stock_movements",
    "return_items",
    "returns",
    "supplier_payments",
    "payments",
    "inventory",
    "purchase_items",
    "purchases",
    "sale_items",
    "sales",
    "suppliers",
    "customers",
    "products",
    "categories",
    "users",
    "roles",
)


def upgrade():
    schema_file = Path(__file__).resolve().parents[3] / "database" / "postgresql_schema.sql"
    sql = re.sub(r"(?m)^\s*--.*$", "", schema_file.read_text(encoding="utf-8"))
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement and statement.upper() not in {"BEGIN", "COMMIT"}:
            op.execute(statement)


def downgrade():
    for table_name in TABLES_REVERSE_ORDER:
        op.execute(f'DROP TABLE IF EXISTS "{table_name}"')
