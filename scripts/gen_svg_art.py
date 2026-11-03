"""Ask the Groq LLM to draw a simple SVG illustration per recipe, sanitise it, and save it to static/img/foods/<id>.svg.
The app picks these up automatically. Run once; existing files are skipped. Needs GROQ_API_KEY.
    python scripts/gen_svg_art.py [--limit 5]"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from core import db, llm  # noqa: E402
from core.svgsafe import sanitize_svg  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "static" / "img" / "foods"
SYSTEM = ("You draw simple flat food illustrations as SVG. Output ONLY the SVG code, nothing else. "
          "Rules: viewBox=\"0 0 100 100\"; transparent background; at most 14 shapes using only path, circle, ellipse, rect, "
          "g, linearGradient, stop; warm friendly colours; a bowl or plate seen slightly from above with the food on it; "
          "no text, no scripts, no images, no external links.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    client = llm.make_client(os.getenv("GROQ_API_KEY"))
    if client is None:
        sys.exit("Set GROQ_API_KEY first (in .env or the environment).")
    conn = db.init(db.connect())
    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    for r in conn.execute("SELECT id, name FROM recipes ORDER BY id"):
        target = OUT / f"{r['id']}.svg"
        if target.exists():
            continue
        try:
            text = llm._ask(client, os.getenv("GROQ_MODEL", llm.DEFAULT_MODEL), SYSTEM, f"Dish: {r['name']}", max_tokens=1800, temperature=0.4)
        except (llm.LLMError, llm.LLMAPIError) as exc:
            print(f"  {r['id']}: {exc}")
            continue
        svg = sanitize_svg(text)
        if svg is None:
            print(f"  {r['id']}: output was not safe/valid SVG, skipped")
            continue
        target.write_text(svg, encoding="utf-8")
        done += 1
        print(f"  {r['id']}: saved")
        if args.limit and done >= args.limit:
            break
        time.sleep(2.5)   # stay under the free-tier rate limit
    print(f"{done} image(s) written to {OUT}. Review them by eye and delete any you dislike.")


if __name__ == "__main__":
    main()
