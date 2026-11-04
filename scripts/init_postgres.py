"""Run after `alembic upgrade head` to idempotently seed shared catalogue data."""
from core import db
conn = db.connect("postgresql")
try:
    db.init(conn)
    print("Seeded shared food and recipe catalogue.")
finally:
    conn.close()
