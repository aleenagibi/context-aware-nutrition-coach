"""Tiny retrieval layer: parse the knowledge base and rank passages with BM25 (pure Python)."""
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge.md"
STOP = set("a an the of to in on for and or is are be how do does should i my what with it at by from as that this can".split())
META = re.compile(r"(source|status):", re.I)


@dataclass
class Chunk:
    id: str
    title: str
    source: str
    status: str
    text: str


def load_kb(path=KB_PATH):
    chunks = []
    for block in re.split(r"^## ", Path(path).read_text(encoding="utf-8"), flags=re.M)[1:]:
        head, *lines = block.strip().splitlines()
        m = re.match(r"\[(S\d+)\]\s*(.+)", head)
        meta = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines if META.match(l)}
        body = " ".join(l.strip() for l in lines if l.strip() and not META.match(l))
        chunks.append(Chunk(m[1], m[2], meta.get("source", ""), meta.get("status", ""), body))
    return chunks


def stem(w):
    """Crude stemming: building -> build, muscles -> muscle."""
    for suffix in ("ing", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    return w


def tokenize(s):
    return [stem(w) for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]


class BM25:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [Counter(tokenize(c.title + " " + c.text)) for c in chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / len(self.lens)
        df = Counter(t for d in self.docs for t in d)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query, k=3):
        q = tokenize(query)
        scored = []
        for doc, length in zip(self.docs, self.lens):
            norm = self.k1 * (1 - self.b + self.b * length / self.avg)
            scored.append(sum(self.idf.get(t, 0) * doc[t] * (self.k1 + 1) / (doc[t] + norm)
                              for t in q if t in doc))
        ranked = sorted(zip(scored, self.chunks), key=lambda x: -x[0])
        return [(round(s, 2), c) for s, c in ranked[:k] if s > 0]
