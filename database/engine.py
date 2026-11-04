"""Engine and compatibility adapter for legacy parameterized SQL while modules are migrated."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Row

from .models import Base, catalogue, personal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
_engine = None

def engine():
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required. Configure PostgreSQL in .env.")
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _engine

class RowProxy:
    def __init__(self, row):
        self._row = row
        self._map = row._mapping if hasattr(row, "_mapping") else row
    def __getitem__(self, key):
        if isinstance(key, int): return tuple(self._map.values())[key]
        return self._map[key]
    def __iter__(self): return iter(self._map.items())
    def keys(self): return self._map.keys()
    def __len__(self): return len(self._map)
    def __repr__(self): return repr(dict(self._map))

class ResultProxy:
    def __init__(self, result):
        self.result = result
        self.lastrowid = None
        try:
            row = result.fetchone()
            if row is not None:
                self.lastrowid = row[0]
                self._first = RowProxy(row)
            else: self._first = None
        except Exception:
            self._first = None
    def fetchone(self):
        if hasattr(self, "_first"):
            row, self._first = self._first, None
            if row is not None: return row
        r = self.result.fetchone()
        return RowProxy(r) if r is not None else None
    def fetchall(self):
        rows = []
        if hasattr(self, "_first") and self._first is not None:
            rows.append(self._first)
            self._first = None
        rows.extend(RowProxy(r) for r in self.result.fetchall())
        return rows
    def __iter__(self):
        first = self.fetchone()
        if first is not None: yield first
        for r in self.result: yield RowProxy(r)
    def __getattr__(self, n): return getattr(self.result, n)

class Connection:
    def __init__(self, schema="public"):
        self.raw = engine().connect()
        self.tx = self.raw.begin()
        self.schema = schema
        self.raw.exec_driver_sql('SET search_path TO "' + schema.replace('"','') + '", public')
    def _prepare(self, sql, params=None):
        q = sql.strip()
        # Translate SQLite positional placeholders to SQLAlchemy named parameters.
        if params is not None and not isinstance(params, dict) and "?" in q:
            parts = q.split("?")
            names = [f"p{i}" for i in range(len(parts)-1)]
            q = "".join(part + (":" + names[i] if i < len(names) else "") for i,part in enumerate(parts))
            params = {name: value for name,value in zip(names, params)}
        # Legacy code expects cursor.lastrowid; PostgreSQL returns it explicitly.
        if q.upper().startswith("INSERT INTO CUSTOM_FOODS") or q.upper().startswith("INSERT INTO MEAL_LOG"):
            if "RETURNING" not in q.upper(): q += " RETURNING id"
        return q, params or {}
    def execute(self, sql, params=None):
        q, p = self._prepare(sql, params)
        return ResultProxy(self.raw.execute(text(q), p))
    def executemany(self, sql, seq):
        last = None
        for params in seq: last = self.execute(sql, params)
        return last
    def executescript(self, sql):
        for stmt in sql.split(";"):
            if stmt.strip(): self.execute(stmt)
    def commit(self):
        if self.tx.is_active: self.tx.commit()
        self.tx = self.raw.begin()
    def rollback(self):
        if self.tx.is_active: self.tx.rollback()
        self.tx = self.raw.begin()
    def close(self):
        if self.tx.is_active: self.tx.rollback()
        self.raw.close()

def initialize_catalogue():
    catalogue.create_all(engine())
def initialize_user_schema(user_id):
    # Shared referenced tables must exist before personal-schema FK creation.
    initialize_catalogue()
    schema = "u_" + re.sub(r"[^a-f0-9]", "", user_id.lower())
    if not schema or schema == "u_": raise ValueError("Invalid user identifier")
    with engine().begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    # create_all must execute with the user's schema as search_path
    with engine().begin() as c:
        c.exec_driver_sql(f'SET search_path TO "{schema}", public')
        personal.create_all(c)
    return schema
def drop_user_schema(user_id):
    schema = "u_" + re.sub(r"[^a-f0-9]", "", user_id.lower())
    with engine().begin() as c: c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
