"""Resolve recipe instructions from source metadata or generate them from known ingredients.

No ingredient quantities or nutrition values are inferred or modified here.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests


MAX_HTML_BYTES = 2_000_000
REQUEST_TIMEOUT = (4, 8)


class _JSONLDParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_jsonld = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            attrs = dict(attrs)
            if "ld+json" in attrs.get("type", "").lower():
                self.in_jsonld = True
                self.parts = []

    def handle_data(self, data):
        if self.in_jsonld:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_jsonld:
            raw = "".join(self.parts).strip()
            if raw:
                try:
                    self.documents.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    pass
            self.in_jsonld = False
            self.parts = []


def _public_http_url(url):
    """Reject non-http, local, private, loopback and link-local destinations."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                return False
    except (OSError, ValueError):
        return False
    return True


def _recipe_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _recipe_nodes(item)
    elif isinstance(value, dict):
        typ = value.get("@type", [])
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower().endswith("recipe") for t in types):
            yield value
        if "@graph" in value:
            yield from _recipe_nodes(value["@graph"])


def _instruction_text(value):
    out = []
    if isinstance(value, str):
        text = " ".join(value.split())
        if text:
            out.append(text)
    elif isinstance(value, list):
        for item in value:
            out.extend(_instruction_text(item))
    elif isinstance(value, dict):
        # HowToStep, HowToSection, and nested schema.org instruction objects.
        if value.get("text"):
            out.extend(_instruction_text(value["text"]))
        elif value.get("name") and value.get("itemListElement"):
            out.extend(_instruction_text(value["itemListElement"]))
        elif value.get("itemListElement"):
            out.extend(_instruction_text(value["itemListElement"]))
    return out


def fetch_source_steps(url):
    if not _public_http_url(url):
        return []
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "FuelRecipeHelper/1.0 (+local nutrition app)",
            "Accept": "text/html,application/xhtml+xml",
        }, stream=True, allow_redirects=False)
        if response.status_code != 200:
            response.close()
            return []
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            response.close()
            return []
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_HTML_BYTES:
                response.close()
                return []
            chunks.append(chunk)
        response.close()
        html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        parser = _JSONLDParser()
        parser.feed(html)
        for doc in parser.documents:
            for recipe in _recipe_nodes(doc):
                steps = _instruction_text(recipe.get("recipeInstructions"))
                # Filter navigation/empty fragments and reject suspiciously short results.
                steps = [s for s in steps if len(s) >= 12]
                if 2 <= len(steps) <= 40:
                    return steps
    except (requests.RequestException, UnicodeError, ValueError):
        return []
    return []


def generate_steps(client, model, recipe_name, ingredients, servings, unmatched_ingredients=()):
    if not client or not ingredients:
        return []
    ingredient_lines = [
        f"- {item['name']}: {float(item['grams']) * float(servings):g} g"
        for item in ingredients
    ]
    system = (
        "You are a careful cooking-instruction writer. Return ONLY a JSON array of 4 to 9 "
        "short, sequential cooking steps. Do not include markdown or commentary. "
        "Use a plausible standard method for the named dish. Do not add ingredients, "
        "invent ingredient quantities, or change the supplied ingredients. You may mention "
        "basic actions such as washing, soaking, grinding, mixing, heating, cooking and serving. "
        "If an essential ingredient or method is ambiguous, state the uncertainty in a step "
        "rather than pretending certainty. Keep food-safety instructions sensible."
    )
    unmatched_lines = [f"- {name}: quantity not verified" for name in unmatched_ingredients]
    user = (
        f"Dish: {recipe_name}\\nServings: {servings:g}\\n"
        f"Matched ingredients and known quantities (fixed):\\n"
        + "\\n".join(ingredient_lines)
        + "\\nAdditional original recipe ingredients with unverified quantities "
          "(include them where relevant; do not invent amounts):\\n"
        + ("\\n".join(unmatched_lines) if unmatched_lines else "- None")
        + "\\nWrite practical recipe-specific steps. Do not omit ingredients simply because their quantities are unknown."
    )
    try:
        from . import llm
        raw = llm._ask(client, model, system, user, max_tokens=900, temperature=0.2)
        parsed = llm._parse_json(raw)
        if not isinstance(parsed, list):
            return []
        steps = [" ".join(str(x).split()) for x in parsed if isinstance(x, str) and len(x.strip()) >= 10]
        return steps[:9] if 4 <= len(steps) <= 9 else []
    except Exception:
        # Generation failure must not break recipe details or show invented placeholder steps.
        return []


def resolve(recipe_name, ingredients, servings, source_url="", groq_api_key="", model="", unmatched_ingredients=()):
    source_steps = fetch_source_steps(source_url)
    if source_steps:
        return source_steps, "source"
    try:
        from . import llm
        client = llm.make_client(groq_api_key)
        generated = generate_steps(client, model or llm.DEFAULT_MODEL, recipe_name, ingredients, servings, unmatched_ingredients)
        if generated:
            return generated, "ai"
    except Exception:
        pass
    return [], "unavailable"
