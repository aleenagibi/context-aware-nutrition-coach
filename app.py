"""Flask app for Nourish, backed by a shared PostgreSQL database.
Run: python app.py"""
import hmac
import json
import os
import re
import secrets
import threading
import uuid
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from flask import (Flask, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for, make_response)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from core import agent, calc, db, foods, images, llm, mealparse, rag, store, suggest, workout, recipe_steps  # noqa: E402
from database.engine import engine, initialize_user_schema, drop_user_schema
from database.models import Base


app = Flask(__name__)

# Keep sessions valid across restarts when no explicit secret is configured.
_secret = os.getenv("FLASK_SECRET_KEY") or secrets.token_urlsafe(48)

app.secret_key = _secret
app.config.update(DB_PATH=str(db.DB_PATH), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14, MAX_CONTENT_LENGTH=64 * 1024)

_AUTH_READY = False
_AUTH_LOCK = threading.Lock()

def auth_connect():
    global _AUTH_READY
    if not _AUTH_READY:
        with _AUTH_LOCK:
            if not _AUTH_READY:
                Base.metadata.create_all(engine())
                _AUTH_READY = True
    return db.connect("postgresql")

def user_record(user_id):
    if not user_id:
        return None
    with closing(auth_connect()) as conn:
        return conn.execute("SELECT id,email FROM users WHERE id=?", (user_id,)).fetchone()

def create_user_db(user_id, first_user=False):
    """Provision a clean personal schema inside the shared PostgreSQL database."""
    initialize_user_schema(user_id)
    conn = db.connect(user_id + ".db")
    try:
        db.init(conn)
    finally:
        conn.close()
    return user_id

def current_user():
    return user_record(session.get("user_id"))

@app.before_request
def require_login():
    endpoint = request.endpoint or ""
    allowed = {"login", "signup", "health", "static"}
    if endpoint in allowed or endpoint.startswith("static"):
        return None
    user = current_user()
    if not user:
        session.pop("user_id", None)
        return redirect(url_for("login", next=request.path))
    g.current_user = user
    return None

KB = rag.load_kb()
BM25 = rag.BM25(KB)
READY = set()
_DB_INIT_LOCK = threading.Lock()
PANELS = ("today", "log", "pantry", "move", "you")
SLOT_LABELS = {"breakfast": "Breakfast", "lunch": "Lunch", "snack": "Snack", "dinner": "Dinner", "main": "Main meal"}
WORKOUT_LABELS = {"strength": "Strength / gym", "running": "Running / jogging", "cycling": "Cycling", "walking": "Walking / hiking",
                  "swimming": "Swimming", "hiit": "HIIT / circuit", "yoga": "Yoga / stretching",
                  "sports": "Sports (badminton, football...)", "other": "Other (dance, elliptical...)"}
SAVE_UNITS = ["g", "ml", "katori", "cup", "bowl", "glass", "piece", "slice", "tbsp", "tsp", "handful", "scoop", "plate"]


# ---------------------------------------------------------------- plumbing
def get_db():
    if "db" not in g:
        uid = session.get("user_id")
        if not uid:
            abort(401)

        if not uid:
            abort(401)
        path = str(uid) + ".db"
        conn = db.connect(path)

        try:
            with _DB_INIT_LOCK:
                if path not in READY:
                    db.init(conn)
                    READY.add(path)

            g.db = conn

        except Exception:
            conn.close()
            raise

    return g.db

@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def csrf_token():
    return session.setdefault("csrf", secrets.token_urlsafe(24))


STATIC_IMG = BASE_DIR / "static" / "img" / "foods"

# Generate recipe photos outside page requests and deduplicate concurrent jobs.
_IMAGE_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="fuel-image")
_IMAGE_LOCK = threading.Lock()
_IMAGE_JOBS = set()


def queue_recipe_image(recipe_id, recipe_name):
    if images.cached_image(recipe_id):
        return
    key = str(recipe_id)
    with _IMAGE_LOCK:
        if key in _IMAGE_JOBS:
            return
        _IMAGE_JOBS.add(key)

    def work():
        try:
            images.generate_recipe_image(recipe_id, recipe_name)
        finally:
            with _IMAGE_LOCK:
                _IMAGE_JOBS.discard(key)

    _IMAGE_POOL.submit(work)


def food_image(recipe_id):
    """URL of a real/generated picture dropped into static/img/foods/<recipe id>.(webp|png|jpg|svg), else None."""
    for ext in ("webp", "png", "jpg", "jpeg", "svg"):
        if (STATIC_IMG / f"{recipe_id}.{ext}").exists():
            return url_for("static", filename=f"img/foods/{recipe_id}.{ext}")
    return None


app.jinja_env.globals.update(csrf_token=csrf_token, food_image=food_image, SLOT_LABELS=SLOT_LABELS)

from flask import send_from_directory, current_app

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        current_app.static_folder,
        "img/logo/nourish-icon.png",
        mimetype="image/png"
    )
@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            abort(400, "Missing or invalid CSRF token. Reload the page and try again.")


class UserError(Exception):
    """A problem the user can fix; shown as a message."""


def number(form, name, label, lo, hi, cast=float):
    try:
        value = cast(form.get(name, "").strip())
    except ValueError:
        raise UserError(f"{label} must be a number.") from None
    if not lo <= value <= hi:
        raise UserError(f"{label} must be between {lo} and {hi}.")
    return value


# ---------------------------------------------------------------- derived state
def current_targets(profile, sessions):
    burn = sum(workout.kcal_burned(s, profile["weight_kg"]) for s in sessions)
    t = calc.compute_targets(profile["sex"], profile["age"], profile["height_cm"], profile["weight_kg"],
                             profile["lifestyle"], profile["goal"], burn, bool(sessions))
    return t, burn


def dashboard(targets, take, minutes):
    logged = take["count"] > 0
    pct = min(100, round(take["protein"] / targets.protein_low * 100)) if logged else 0
    left = max(0, round(targets.protein_low - take["protein"])) if logged else targets.protein_low
    trained = f" You moved for {minutes} min today." if minutes else ""
    if not logged:
        head, sub = "What will you eat next?", f"Target today: {targets.protein_low}-{targets.protein_high} g protein. Nothing logged yet."
    elif left > 0:
        head, sub = f"About {left} g protein to go", f"Roughly {round(take['protein_lo'])}-{round(take['protein_hi'])} g logged so far.{trained}"
    else:
        head, sub = "Low end reached", f"Roughly {round(take['protein_lo'])}-{round(take['protein_hi'])} g logged.{trained}"
    return {"logged": logged, "pct": pct, "left": left, "headline": head, "sub": sub,
            "kcal_pct": min(100, round(take["kcal"] / targets.calories * 100)) if targets.calories else 0}


def build_ctx(conn):
    profile = store.get_profile(conn)
    sess = store.sessions(conn)
    targets, burn = current_targets(profile, sess)
    take = store.intake(conn)
    pantry = store.pantry_ids(conn)
    minutes = round(sum(s.duration_min for s in sess))
    dash = dashboard(targets, take, minutes)
    ideas = suggest.rank(conn, profile, pantry, dash["left"], max(0, targets.calories - take["kcal"]), n=6)
    for idea in ideas:
        queue_recipe_image(idea["recipe"].id, idea["recipe"].name)
    recipes = {r.id: r for r in suggest.load_recipes(conn)}
    plan = []
    for row in store.plan_rows(conn):
        r = recipes[row["recipe_id"]]
        plan.append({**row, "recipe": r, "protein": round(r.protein * row["servings"]), "kcal": round(r.kcal * row["servings"]),
                     "done": bool(row["log_id"])})
    return {"profile": profile, "targets": targets, "burn": round(burn), "take": take, "dash": dash, "minutes": minutes,
            "ideas": ideas, "plan": plan, "pantry": pantry, "has_key": bool(os.getenv("GROQ_API_KEY")),
            "model": os.getenv("GROQ_MODEL", llm.DEFAULT_MODEL), "log": store.log_rows(conn), "recent": store.recent_meals(conn),
            "workouts": store.workout_rows(conn), "workout_labels": WORKOUT_LABELS, "categories": workout.CATEGORIES,
            "pantry_groups": foods.pantry_foods(conn), "custom_pantry": store.custom_pantry_foods(conn),
            "diets": list(foods.DIETS), "lifestyles": list(calc.LIFESTYLE),
            "goals": list(calc.GOALS), "chat": store.chat_history(conn, 20), "chat_meta": store.kv_get(conn, "chat_meta", {}),
            "kb": KB, "foods_dense": foods.protein_dense(conn, profile["diet"], 8)}


def render_panel(name, ctx):
    return render_template(f"panels/{name}.html", **ctx)


def summary_for_agent(conn):
    profile, sess = store.get_profile(conn), store.sessions(conn)
    targets, _ = current_targets(profile, sess)
    take = store.intake(conn)
    meals = store.log_rows(conn)  # today's entries only; store.log_rows defaults to the current day
    movement = store.workout_rows(conn)  # today's workouts only
    return {"date": store.today(),
            "protein_logged_low": take["protein_lo"], "protein_logged_high": take["protein_hi"],
            "kcal_logged": take["kcal"], "carbs_logged": take["carbs"], "fat_logged": take["fat"],
            "protein_target_low": targets.protein_low, "protein_target_high": targets.protein_high,
            "kcal_target": targets.calories, "protein_left": max(0, round(targets.protein_low - take["protein"])),
            "meals_today": [{"meal": r["label"], "kcal": r["kcal"], "protein_g": r["protein"],
                             "carbs_g": r["carbs"], "fat_g": r["fat"], "servings": r["servings"]} for r in meals],
            "workouts_today": [{"activity": r["activity"] or r["category"], "minutes": r["minutes"],
                                "intensity": r["intensity"]} for r in movement]}


# ---------------------------------------------------------------- actions
@dataclass
class Outcome:
    msg: str = ""
    cat: str = "success"
    panels: object = None          # None = refresh all panels, () = none
    sheet: str = None
    close: bool = False
    tab: str = "today"


def act_save_profile(conn, form):
    p = {"sex": form.get("sex"), "lifestyle": form.get("lifestyle"), "goal": form.get("goal"), "diet": form.get("diet"),
         "age": number(form, "age", "Age", 18, 80, int), "height_cm": number(form, "height_cm", "Height", 120, 220),
         "weight_kg": number(form, "weight_kg", "Weight", 35, 200), "cook_minutes": number(form, "cook_minutes", "Cooking time", 5, 120, int),
         "dislikes": form.get("dislikes", "").strip()[:200]}
    if p["sex"] not in ("Male", "Female") or p["lifestyle"] not in calc.LIFESTYLE or p["goal"] not in calc.GOALS or p["diet"] not in foods.DIETS:
        raise UserError("Please choose valid options from the lists.")
    store.save_profile(conn, p)
    return Outcome("Profile saved.", tab="you")


def act_set_pantry(conn, form):
    store.set_pantry(conn, form.getlist("food"))
    store.set_custom_pantry(conn, form.getlist("custom_food"))
    return Outcome("Pantry updated.", cat="success", panels=("today", "pantry"), tab="pantry")


def act_add_custom_food(conn, form):
    name = form.get("name", "")
    def optional_number(key):
        raw = form.get(key, "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            raise UserError(f"{key.capitalize()} must be a number.") from None
        if not 0 <= value <= 1000:
            raise UserError(f"{key.capitalize()} must be between 0 and 1000 per 100 g.")
        return value
    try:
        store.add_custom_food(conn, name, optional_number("kcal"), optional_number("protein"),
                              optional_number("carbs"), optional_number("fat"), form.get("mapped_food_id") or None)
    except ValueError as exc:
        raise UserError(str(exc)) from None
    return Outcome("Custom food added to your pantry.", cat="success", panels=("pantry", "today"), tab="pantry")


def act_log_recipe(conn, form):
    r = suggest.get_recipe(conn, form.get("recipe_id", ""))
    if not r:
        raise UserError("Recipe not found.")
    servings = number(form, "servings", "Servings", 0.5, 4)
    m = store.recipe_macros(r, servings)
    store.add_log(conn, r.name if servings == 1 else f"{r.name} x{servings:g}", m["kcal"], m["protein"], m["carbs"], m["fat"],
                  r.err, "recipe", r.id, servings)
    return Outcome(f"Logged {r.name}.", close=True)


def act_log_again(conn, form):
    recent = store.recent_meals(conn)
    idx = number(form, "index", "Meal", 0, max(len(recent) - 1, 0), int)
    if not recent:
        raise UserError("Nothing to log again yet.")
    m = recent[idx]
    store.add_log(conn, m["label"], m["kcal"], m["protein"], m["carbs"], m["fat"], m["err"], m["source"], m["ref_id"], m["servings"])
    return Outcome(f"Logged {m['label']} again.", tab="log")


def act_delete_log(conn, form):
    store.delete_log(conn, number(form, "id", "Entry", 1, 10**9, int))
    return Outcome("Removed.", tab=form.get("tab", "today"))


def food_choices(conn):
    return [dict(r) for r in conn.execute("SELECT id, name FROM foods ORDER BY name")]


def act_parse_meal(conn, form):
    text = form.get("meal_text", "").strip()[:600]
    if not text:
        raise UserError("Type what you ate first.")
    client, parsed, how = llm.make_client(os.getenv("GROQ_API_KEY")), None, "basic parser"
    note = ""
    if client:
        try:
            parsed, how = llm.extract_meals(client, text, os.getenv("GROQ_MODEL", llm.DEFAULT_MODEL)), "AI reader"
        except (llm.LLMError, llm.LLMAPIError) as exc:
            note = f"{exc} Used the basic parser instead."
    if parsed is None:
        parsed = mealparse.regex_parse_meal(text)
    items = mealparse.resolve(conn, parsed)
    if not items:
        raise UserError("I could not find any foods in that text. Try listing them, like '2 roti, 1 katori dal'.")
    for it in items:
        it["unit_options"] = ["", *SAVE_UNITS, *sorted(set(foods.food_units(conn, it["food_id"])) - set(SAVE_UNITS))] if it.get("food_id") else ["", *SAVE_UNITS]
        if it.get("unit") and it["unit"] not in it["unit_options"]:
            it["unit_options"].append(it["unit"])
    html = render_template("partials/meal_confirm.html", items=items, foods=food_choices(conn), how=how, note=note,
                           totals=mealparse.totals(items), text=text)
    return Outcome(panels=(), sheet=html)


def act_save_meal(conn, form):
    saved, skipped = 0, 0
    for i in range(number(form, "n", "Items", 0, 30, int)):
        food_id = form.get(f"food_{i}", "")
        if not form.get(f"inc_{i}") or not food_id:
            skipped += 1
            continue
        food = foods.get_food(conn, food_id)
        if not food:
            continue
        qty_raw = form.get(f"qty_{i}", "").strip()
        try:
            qty = float(qty_raw) if qty_raw else None
        except ValueError:
            raise UserError("Quantities must be numbers.") from None
        if qty is not None and not 0 < qty <= 5000:
            raise UserError("Quantities must be between 0 and 5000.")
        item = mealparse.build_item(conn, food, qty, form.get(f"unit_{i}") or None)
        store.add_log(conn, f"{food['name']} ({item['grams']} g)", item["kcal"], item["protein"], item["carbs"], item["fat"],
                      item["err"], "text", food["id"])
        saved += 1
    if not saved:
        raise UserError("Nothing selected to save.")
    return Outcome(f"Logged {saved} item(s)." + (f" Skipped {skipped}." if skipped else ""), close=True, tab="log")


def act_add_workouts(conn, form):
    added = 0
    for cat in form.getlist("cat"):
        if cat in workout.CATEGORIES:
            minutes = number(form, f"min_{cat}", "Minutes", 1, 480)
            intensity = form.get(f"int_{cat}", "moderate")
            store.add_workout(conn, workout.make_session({"activity": WORKOUT_LABELS[cat], "category": cat,
                                                           "duration_min": minutes, "intensity": intensity}))
            added += 1
    if not added:
        raise UserError("Tick at least one activity.")
    return Outcome(f"Added {added} session(s).", tab="move")


def act_parse_workout(conn, form):
    text = form.get("workout_text", "").strip()[:1000]
    if not text:
        raise UserError("Describe your workout first.")
    client, parsed, note = llm.make_client(os.getenv("GROQ_API_KEY")), None, ""
    if client:
        try:
            parsed = llm.extract_workouts(client, text, os.getenv("GROQ_MODEL", llm.DEFAULT_MODEL))
        except (llm.LLMError, llm.LLMAPIError) as exc:
            note = f" ({exc} Used the basic parser.)"
    if parsed is None:
        parsed = workout.regex_parse(text)
    for s in parsed:
        store.add_workout(conn, s)
    if not parsed:
        raise UserError("No workout with a duration found. Try '45 min jog' or use the tick boxes above.")
    return Outcome(f"Added {len(parsed)} session(s).{note}", tab="move")


def act_delete_workout(conn, form):
    store.delete_workout(conn, number(form, "id", "Session", 1, 10**9, int))
    return Outcome(tab="move")


def act_build_plan(conn, form):
    ctx = build_ctx(conn)
    plan = suggest.build_plan(conn, ctx["profile"], ctx["targets"], ctx["pantry"])
    if not plan["items"]:
        raise UserError("No recipes match your diet and dislikes. Relax a preference and try again.")
    store.save_plan(conn, plan["items"])
    msg = "Plan ready." if plan["meets_protein"] else "Plan ready, but it falls short of your protein target with these recipes."
    return Outcome(msg, cat="success" if plan["meets_protein"] else "warning")


def act_tick_plan(conn, form):
    item_id = number(form, "id", "Item", 1, 10**9, int)
    row = conn.execute("SELECT * FROM plan_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise UserError("Plan item not found.")
    r = suggest.get_recipe(conn, row["recipe_id"])
    done = form.get("done") == "1"
    store.set_plan_done(conn, item_id, done, store.recipe_macros(r, row["servings"]) if done else None,
                        r.name if row["servings"] == 1 else f"{r.name} x{row['servings']:g}", r.err)
    return Outcome()


def act_chat(conn, form):
    question = form.get("question", "").strip()[:500]
    if not question:
        raise UserError("Type a question first.")
    client = llm.make_client(os.getenv("GROQ_API_KEY"))
    if not client:
        raise UserError("The coach needs a Groq key. Add GROQ_API_KEY to the .env file and restart.")
    profile = store.get_profile(conn)
    box = agent.Toolbox(conn, BM25, profile, lambda: summary_for_agent(conn), store.pantry_ids(conn))
    history = store.chat_history(conn, 12)
    out = agent.chat(client, history, question, {k: profile[k] for k in ("sex", "age", "height_cm", "weight_kg", "lifestyle", "goal", "diet")},
                     box, os.getenv("GROQ_MODEL", llm.DEFAULT_MODEL))
    store.add_chat(conn, "user", question)
    store.add_chat(conn, "assistant", out["text"])
    store.kv_set(conn, "chat_meta", {"trace": out["trace"], "unsupported": out["unsupported_citations"],
                                     "sources": [{"id": c.id, "title": c.title, "status": c.status} for c in out["sources"]]})
    return Outcome(tab="you")


def act_clear_chat(conn, form):
    store.clear_chat(conn)
    return Outcome(tab="you")


def act_wipe(conn, form):
    if form.get("confirm", "").strip().lower() != "delete":
        raise UserError("Type DELETE to confirm.")
    store.wipe_user_data(conn)
    return Outcome("All your data was deleted.", tab="you")


ACTIONS = {f[4:]: fn for f, fn in list(globals().items()) if f.startswith("act_")}


def is_ajax():
    return request.headers.get("X-Requested-With") == "fetch"


@app.post("/act/<name>")
def act(name):
    handler = ACTIONS.get(name)
    conn = get_db()
    try:
        if handler is None:
            abort(404)
        out = handler(conn, request.form)
    except UserError as exc:
        out = Outcome(str(exc), "error", panels=(), tab=request.form.get("tab", "today"))
    except (llm.LLMError, llm.LLMAPIError) as exc:
        out = Outcome(str(exc), "error", panels=(), tab=request.form.get("tab", "today"))
    except Exception:  # never show raw exception text to the user
        app.logger.exception("Unhandled error in action %s", name)
        out = Outcome("Something went wrong. Check the server log for details.", "error", panels=(), tab=request.form.get("tab", "today"))
    if is_ajax():
        wanted = PANELS if out.panels is None else out.panels
        ctx = build_ctx(conn) if wanted else {}
        return jsonify({"ok": out.cat != "error", "flash": [out.cat, out.msg] if out.msg else None,
                        "panels": {p: render_panel(p, ctx) for p in wanted}, "sheet": out.sheet, "close": out.close})
    if out.msg:
        flash(out.msg, out.cat)
    if out.sheet:
        flash("Confirming a typed meal needs JavaScript. Please enable it.", "warning")
    return redirect(url_for("index") + "#" + (out.tab if out.tab in PANELS else "today"))



# ---------------------------------------------------------------- authentication
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user_id") and current_user():
        return redirect(url_for("index"))
    error = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            error = "Enter a valid email address."
        elif len(password) < 10 or len(password) > 128:
            error = "Use a password between 10 and 128 characters."
        elif password != confirm:
            error = "The passwords do not match."
        else:
            with closing(auth_connect()) as accounts:
                if accounts.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (email,)).fetchone():
                    error = "An account with that email already exists. Try logging in."
                else:
                    first_user = accounts.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
                    uid = uuid.uuid4().hex
                    try:
                        create_user_db(uid, first_user=first_user)
                        accounts.execute("INSERT INTO users(id,email,password_hash) VALUES(?,?,?)",
                                         (uid, email, generate_password_hash(password)))
                        accounts.commit()
                    except Exception:
                        try:
                            drop_user_schema(uid)
                        except Exception:
                            app.logger.exception("Could not clean up an incomplete user schema")
                        app.logger.exception("Could not create Nourish account")
                        error = "We couldn't create your account. Please try again."
                    else:
                        session.clear()
                        session["user_id"] = uid
                        session.permanent = True
                        return redirect(url_for("index"))
    return render_template("auth.html", mode="signup", error=error)

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id") and current_user():
        return redirect(url_for("index"))
    error = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with closing(auth_connect()) as accounts:
            account = accounts.execute("SELECT id,password_hash FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
        if not account or not check_password_hash(account["password_hash"], password):
            error = "Email or password is incorrect."
        else:
            session.clear()
            session["user_id"] = account["id"]
            session.permanent = True
            return redirect(url_for("index"))
    return render_template("auth.html", mode="login", error=error)

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------- pages
@app.get("/")
def index():
    ctx = build_ctx(get_db())
    return render_template("base.html", panel_names=PANELS, panels={p: render_panel(p, ctx) for p in PANELS})


@app.get("/food-image/<recipe_id>")
def recipe_image(recipe_id):
    cached = images.cached_image(recipe_id)
    if cached:
        response = make_response(send_file(cached, conditional=True))
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    recipe = suggest.get_recipe(get_db(), recipe_id)
    if not recipe:
        abort(404)
    queue_recipe_image(recipe_id, recipe.name)
    response = make_response("", 202)
    response.headers["Retry-After"] = "2"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/recipe/<recipe_id>")
def recipe_detail(recipe_id):
    conn = get_db()
    r = suggest.get_recipe(conn, recipe_id)
    if not r:
        abort(404)
    try:
        servings = min(4.0, max(0.5, float(request.args.get("servings", 1))))
    except ValueError:
        servings = 1.0
    # Generate in the background; opening a recipe never waits for image generation.
    queue_recipe_image(recipe_id, r.name)
    # Imported recipes have no verified cooking method until we retrieve it from
    # the original Recipe page or generate constrained, recipe-specific steps.
    if r.steps_source == "pending":
        resolved_steps, resolved_source = recipe_steps.resolve(
            recipe_name=r.name,
            ingredients=r.items,
            unmatched_ingredients=r.extras,
            servings=servings,
            source_url=r.source_url,
            groq_api_key=os.getenv("GROQ_API_KEY"),
            model=os.getenv("GROQ_MODEL", llm.DEFAULT_MODEL),
        )
        if resolved_steps:
            conn.execute("UPDATE recipes SET steps=?, steps_source=? WHERE id=?",
                         (json.dumps(resolved_steps, ensure_ascii=False), resolved_source, recipe_id))
            conn.commit()
            r.steps = resolved_steps
            r.steps_source = resolved_source
    have, need, cov = suggest.coverage(r, store.pantry_ids(conn))
    return render_template("partials/recipe.html", r=r, servings=servings, have=have, need=need, video=suggest.video_url(r.name),
                           protein=round(r.protein * servings), kcal=round(r.kcal * servings),
                           lo=round(r.protein * servings * (1 - r.err)), hi=round(r.protein * servings * (1 + r.err)))


@app.get("/export.json")
def export():
    resp = jsonify(store.export_all(get_db()))
    resp.headers["Content-Disposition"] = "attachment; filename=fuel-data.json"
    return resp


@app.get("/health")
def health():
    return {"status": "ok", "groq_key_configured": bool(os.getenv("GROQ_API_KEY")),
            "pollinations_key_configured": bool(os.getenv("POLLINATIONS_API_KEY"))}


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true", host="127.0.0.1", port=int(os.getenv("PORT", "5000")))
