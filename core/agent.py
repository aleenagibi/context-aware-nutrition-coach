"""Groq tool-calling nutrition coach. Numbers come from Python tools, never from the model."""
import json
import re
from dataclasses import asdict

from . import calc, foods as food_data, suggest, workout
from .llm import DEFAULT_MODEL, create_with_retry

_WORKOUT_ITEM = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": workout.CATEGORIES},
                   "duration_min": {"type": "number"},
                   "intensity": {"type": "string", "enum": workout.INTENSITIES}},
    "required": ["category", "duration_min", "intensity"], "additionalProperties": False,
}


def _tool(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(required), "additionalProperties": False}}}


# Groq's Chat Completions API uses OpenAI-compatible function tool definitions.
TOOLS = [
    _tool("compute_targets", "Calculate daily calorie and protein targets. Use for any 'how much should I eat' or what-if question.",
          {"sex": {"type": "string", "enum": ["Male", "Female"]}, "age": {"type": "integer", "minimum": 18},
           "height_cm": {"type": "number"}, "weight_kg": {"type": "number"},
           "lifestyle": {"type": "string", "enum": list(calc.LIFESTYLE)}, "goal": {"type": "string", "enum": list(calc.GOALS)},
           "workouts": {"type": "array", "description": "Empty list means rest day", "items": _WORKOUT_ITEM}},
          ["sex", "age", "height_cm", "weight_kg", "lifestyle", "goal", "workouts"]),
    _tool("lookup_food", "Get calories and macros for a food from the database, for a quantity in grams or a unit (roti, katori, glass...).",
          {"name": {"type": "string"}, "grams": {"type": "number", "minimum": 1, "maximum": 1000},
           "quantity": {"type": "number"}, "unit": {"type": "string"}}, ["name"]),
    _tool("today_summary", "Return only the current day’s logged meals, workouts, nutrition totals and targets. Never retrieve historical logs.", {}),
    _tool("suggest_recipes", "Recipes that fit the user's diet, remaining protein and pantry, with ingredients they have or need.",
          {"count": {"type": "integer", "minimum": 1, "maximum": 5}}),
    _tool("search_knowledge", "Search the nutrition knowledge base. Use before stating any guideline, range or recommendation.",
          {"query": {"type": "string"}}, ["query"]),
]


class Toolbox:
    """Executes tool calls against the database. The user's diet is enforced here, not trusted to the model."""

    def __init__(self, conn, bm25, profile, summary_fn=None, pantry=None):
        self.conn, self.bm25, self.profile, self.summary_fn = conn, bm25, profile, summary_fn
        self.pantry = pantry or set()
        self.retrieved = {}

    def call(self, name, args):
        try:
            return getattr(self, "_" + name)(**args)
        except Exception as exc:  # errors go back to the model so it can recover
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _compute_targets(self, sex, age, height_cm, weight_kg, lifestyle, goal, workouts):
        sessions = [workout.make_session(w) for w in workouts]
        burn = sum(workout.kcal_burned(s, weight_kg) for s in sessions)
        return asdict(calc.compute_targets(sex, age, height_cm, weight_kg, lifestyle, goal, burn, bool(sessions)))

    def _lookup_food(self, name, grams=None, quantity=None, unit=None):
        found = food_data.find_food(self.conn, name)
        food = found["food"]
        if food is None:
            return {"error": "food not in the database", "similar": [c["name"] for c in found["candidates"]]}
        if food["diet_level"] > food_data.DIETS.get(self.profile.get("diet"), 3):
            return {"error": "this food does not fit the user's diet; do not suggest it"}
        g, assumed = (float(grams), False) if grams else food_data.grams_for(self.conn, food, quantity, unit)
        return {"food": food["name"], "grams": round(g), "portion_assumed": assumed, "confidence": food["confidence"],
                **food_data.macros(food, g)}

    def _today_summary(self):
        return self.summary_fn() if self.summary_fn else {"error": "no summary available"}

    def _suggest_recipes(self, count=3):
        left = (self.summary_fn() or {}).get("protein_left", 30) if self.summary_fn else 30
        top = suggest.rank(self.conn, self.profile, self.pantry, left, None, n=count)
        return [{"name": d["recipe"].name, "protein_g": d["recipe"].protein, "kcal": d["recipe"].kcal,
                 "minutes": d["recipe"].minutes, "have": d["have"], "need": d["need"]} for d in top]

    def _search_knowledge(self, query):
        out = []
        for _, c in self.bm25.search(query, 3):
            self.retrieved[c.id] = c
            out.append({"id": c.id, "title": c.title, "status": c.status, "text": c.text})
        return out or {"error": "nothing relevant found"}


def system_prompt(profile):
    return f"""You are a careful sports-nutrition coach inside a calorie/protein planner.
User profile: {profile}
Rules:
- Scope: use the current day's records only. Do not retrieve, infer, summarise or claim access to previous days, weekly trends or long-term history. The profile and saved preferences are current settings, not historical tracking.
- If asked about past days, weekly progress or weight trends, explain that historical context is not enabled yet.
- Never assume an unlogged meal or workout happened.
- Never do nutrition arithmetic yourself. Use compute_targets, lookup_food, today_summary and suggest_recipes for every number.
- Before stating a guideline, range or recommendation, call search_knowledge, and cite passage ids like [S1].
  Only cite ids returned by search_knowledge in this conversation. Never invent a source.
- If a passage status says unchecked, say the figure is a general estimate worth verifying.
- If the knowledge base has nothing relevant, say you are not sure instead of guessing.
- Tools only return foods and recipes that fit the user's diet. Never suggest anything outside it.
- Do not invent recipes, links or nutrition values. Recipe suggestions come from suggest_recipes only.
- Not medical advice. For medical conditions, pregnancy, under-18s or eating-disorder history, advise a professional.
- Be concise and plain.
- When answering about what the user ate today, call today_summary first. Distinguish estimated food values from verified measurements.
- Do not claim the pantry contains an ingredient unless it is returned by suggest_recipes or another available tool."""


def _message_dict(message):
    """Convert SDK message objects to plain dicts for the next API turn."""
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return message
    result = {"role": getattr(message, "role", "assistant"), "content": getattr(message, "content", None)}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        result["tool_calls"] = [tc.model_dump(exclude_none=True) if hasattr(tc, "model_dump") else tc for tc in tool_calls]
    return result


def chat(client, history, question, profile, toolbox, model=DEFAULT_MODEL, max_steps=6):
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": question})
    trace = []
    for _ in range(max_steps):
        response = create_with_retry(
            client, model=model,
            messages=[{"role": "system", "content": system_prompt(profile)}, *messages],
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1200,
            temperature=0.2,
        )
        try:
            message = response.choices[0].message
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError("Groq returned an unexpected chat response") from exc
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            answer = getattr(message, "content", None) or "I couldn't generate a response. Please try again."
            cited = set(re.findall(r"\[(S\d+)\]", answer))
            return {"text": answer, "trace": trace, "sources": list(toolbox.retrieved.values()),
                    "unsupported_citations": sorted(cited - set(toolbox.retrieved))}

        messages.append(_message_dict(message))
        for tool_call in tool_calls:
            function = tool_call.function
            name = function.name
            try:
                arguments = json.loads(function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = None
                output = {"error": "tool arguments were not valid JSON"}
            else:
                output = toolbox.call(name, arguments)
            trace.append({"tool": name, "input": arguments, "output": output})
            messages.append({"role": "tool", "tool_call_id": tool_call.id,
                             "content": json.dumps(output, ensure_ascii=False, default=str)})
    return {"text": "I could not finish within the step limit. Try a simpler question.", "trace": trace,
            "sources": list(toolbox.retrieved.values()), "unsupported_citations": []}
