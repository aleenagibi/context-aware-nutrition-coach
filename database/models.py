"""SQLAlchemy 2.x schema definitions. Catalogue is shared; personal data is namespaced per user."""
from sqlalchemy import (MetaData, Table, Column, String, Integer, Float, Text, ForeignKey,
                        UniqueConstraint, CheckConstraint, text)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import uuid

class Base(DeclarativeBase):
    metadata = MetaData()

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=text("CURRENT_TIMESTAMP"))

catalogue = MetaData(schema="public")
foods = Table("foods", catalogue,
    Column("id", String, primary_key=True), Column("name", String, nullable=False), Column("state", String),
    Column("category", String), Column("diet_level", Integer, nullable=False, server_default="3"),
    Column("kcal", Float, nullable=False), Column("protein", Float, nullable=False), Column("carbs", Float, nullable=False),
    Column("fat", Float, nullable=False), Column("fibre", Float, server_default="0"), Column("confidence", String, server_default="low"),
    Column("pantry", Integer, server_default="0"), Column("staple", Integer, server_default="0"), Column("pantry_key", String),
    Column("default_unit", String, server_default="serving"), Column("source", String), Column("source_id", String))
food_aliases = Table("food_aliases", catalogue, Column("alias", String, primary_key=True),
    Column("food_id", String, ForeignKey("public.foods.id", ondelete="CASCADE"), primary_key=True))
food_units = Table("food_units", catalogue, Column("food_id", String, ForeignKey("public.foods.id", ondelete="CASCADE"), primary_key=True),
    Column("unit", String, primary_key=True), Column("grams", Float, nullable=False))
recipes = Table("recipes", catalogue, Column("id", String, primary_key=True), Column("name", String, nullable=False),
    Column("slot", String, nullable=False), Column("minutes", Integer), Column("art", String), Column("steps", Text),
    Column("extras", Text), Column("steps_source", String, nullable=False, server_default="manual"))
recipe_items = Table("recipe_items", catalogue, Column("recipe_id", String, ForeignKey("public.recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("food_id", String, ForeignKey("public.foods.id"), primary_key=True), Column("grams", Float, nullable=False))
recipe_nutrition_reference = Table("recipe_nutrition_reference", catalogue,
    Column("recipe_id", String, ForeignKey("public.recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("kcal", Float), Column("protein", Float), Column("carbs", Float), Column("fat", Float), Column("fibre", Float),
    Column("serving_unit", String), Column("source", String, nullable=False))
recipe_sources = Table("recipe_sources", catalogue, Column("recipe_id", String, ForeignKey("public.recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("source_url", String, nullable=False))

personal = MetaData()
# Lightweight external-table declarations let SQLAlchemy emit cross-schema FK constraints.
Table("foods", personal, Column("id", String, primary_key=True), schema="public")
Table("recipes", personal, Column("id", String, primary_key=True), schema="public")
def personal_table(name, *columns, **kw):
    return Table(name, personal, *columns, **kw)

profile = personal_table("profile", Column("id", Integer, primary_key=True), Column("sex", String), Column("age", Integer),
    Column("height_cm", Float), Column("weight_kg", Float), Column("lifestyle", String), Column("goal", String),
    Column("diet", String), Column("dislikes", String, server_default=""), Column("cook_minutes", Integer, server_default="30"))
pantry = personal_table("pantry", Column("food_id", String, ForeignKey("public.foods.id", ondelete="CASCADE"), primary_key=True))
meal_log = personal_table("meal_log", Column("id", Integer, primary_key=True, autoincrement=True), Column("day", String, nullable=False),
    Column("ts", String, nullable=False), Column("label", String, nullable=False), Column("kcal", Float), Column("protein", Float),
    Column("carbs", Float), Column("fat", Float), Column("err", Float, server_default="0.15"), Column("source", String),
    Column("ref_id", String), Column("servings", Float, server_default="1"))
workouts = personal_table("workouts", Column("id", Integer, primary_key=True, autoincrement=True), Column("day", String, nullable=False),
    Column("activity", String), Column("category", String, nullable=False), Column("minutes", Float, nullable=False), Column("intensity", String, nullable=False))
plan_items = personal_table("plan_items", Column("id", Integer, primary_key=True, autoincrement=True), Column("day", String, nullable=False),
    Column("slot", String, nullable=False), Column("recipe_id", String, ForeignKey("public.recipes.id"), nullable=False),
    Column("servings", Float, nullable=False), Column("log_id", Integer, ForeignKey("meal_log.id", ondelete="SET NULL")))
chat = personal_table("chat", Column("id", Integer, primary_key=True, autoincrement=True), Column("role", String, nullable=False), Column("content", Text, nullable=False))
kv = personal_table("kv", Column("key", String, primary_key=True), Column("value", Text))
custom_foods = personal_table("custom_foods", Column("id", Integer, primary_key=True, autoincrement=True), Column("name", String, nullable=False),
    Column("kcal", Float), Column("protein", Float), Column("carbs", Float), Column("fat", Float),
    Column("mapped_food_id", String, ForeignKey("public.foods.id", ondelete="SET NULL")), Column("in_pantry", Integer, server_default="0"),
    Column("created_at", String, server_default=text("CURRENT_TIMESTAMP")))
