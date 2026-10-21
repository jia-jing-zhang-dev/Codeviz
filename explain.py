"""
Ask Claude to NAME and EXPLAIN structures that analyzer.py already produced.

Order matters: the geometry is built by rules first (analyzer.py knows no
concepts); Claude only looks at the finished shapes afterwards. It cannot
change a cell, a law or a relation, only describe them. Claude is also asked
to flag when the code's names suggest something the geometry does not show.

    python explain.py demo.py              # call Claude, write demo.py.explain.json
    python explain.py demo.py --dry-run    # print the request, call nothing
    python explain.py demo.py --lang en    # explanations in English (default: zh)

Needs `pip install anthropic` and credentials (ANTHROPIC_API_KEY or `ant auth login`).
Results are cached per structure fingerprint, so re-running on unchanged code is free.
"""
import ast
import hashlib
import json
import os
import sys

import analyzer

MODEL = "claude-opus-5-5"

SYSTEM = """You explain program structure to students.

Each structure below was produced by a rule-based analyzer that knows NO concept names.
Its only rules: a whole quantity is a length; multiplying spans a new direction; adding
lays lengths end to end; a loop variable is a position on an axis; accumulating places
each contribution at its loop position; nested collections add directions; only executed
code counts; a call sits one level below its caller and successive calls lie end to end.
"Same shape" and "same count" links were found by comparing outputs, never by names.

Your job is to name and explain what emerged, after the fact:
- Base every claim on the geometry you are given (cells, directions, count law, pieces,
  relations). Do not describe a shape the cells do not show.
- Use the code only to connect the shape back to the lines that produced it.
- If an identifier suggests something the geometry contradicts, set matches_code to false
  and say what the geometry actually shows.
- For each relation group, explain why structurally different code yields the same shape
  or count. This is the most valuable part for students.
- Keep each explanation to two or three sentences a first-year student can follow."""

SCHEMA = {
    "type": "object",
    "properties": {
        "structures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "shape_name": {"type": "string"},
                    "concept": {"type": "string"},
                    "explanation": {"type": "string"},
                    "matches_code": {"type": "boolean"},
                },
                "required": ["id", "shape_name", "concept", "explanation", "matches_code"],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "members": {"type": "array", "items": {"type": "string"}},
                    "insight": {"type": "string"},
                },
                "required": ["members", "insight"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["structures", "relations"],
    "additionalProperties": False,
}


def picture(st):
    """Small text drawing of the cells, so Claude sees the shape itself."""
    d = len(st["axes"])
    cells = {tuple(c[:d]) for c in st["cells"]}
    if d == 0:
        return "(single point)" if cells else "(empty)"
    if d == 1:
        return "#" * len(cells)
    if d == 2:
        mx = max(c[0] for c in cells)
        my = max(c[1] for c in cells)
        if mx > 40 or my > 20:
            return f"({mx + 1} x {my + 1} grid, {len(cells)} filled; too large to draw)"
        return "\n".join("".join("#" if (x, y) in cells else "." for x in range(mx + 1))
                         for y in range(my, -1, -1))
    layers = {}
    for c in cells:
        layers[c[2]] = layers.get(c[2], 0) + 1
    return "3D, cells per layer bottom->top: " + " ".join(str(layers[k]) for k in sorted(layers))


def code_block(src, line):
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in tree.body:
        if node.lineno <= line <= (node.end_lineno or node.lineno):
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return lines[line - 1] if 0 < line <= len(lines) else ""


def describe(scene, src):
    """Everything Claude may look at, one entry per structure."""
    out = []
    for st in scene["structures"]:
        out.append({
            "id": st["id"],
            "title": st["title"],
            "directions": st["axes"],
            "count": st["count"],
            "count_law_in_N": st["law"],
            "pieces": st["regions"] if len(st["regions"]) > 1 else [],
            "same_shape_as": st["same_shape_as"],
            "same_count_as": st["same_law_as"],
            "picture": picture(st),
            "code": code_block(src, st["line"]),
        })
    return out


def fingerprint(entries, lang):
    blob = json.dumps({"lang": lang, "model": MODEL, "entries": entries}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_path(src_path):
    return src_path + ".explain.json"


def load_cached(src_path, src, lang="zh"):
    """Explanations for the current code, or None if missing or stale."""
    try:
        with open(cache_path(src_path), encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return None
    scene = analyzer.build_scene(src)
    if cached.get("key") != fingerprint(describe(scene, src), lang):
        return None
    return cached["result"]


def build_request(entries, lang):
    language = "Simplified Chinese" if lang == "zh" else "English"
    return {
        "model": MODEL,
        "max_tokens": 16000,
        "system": SYSTEM,
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{
            "role": "user",
            "content": f"Write all prose fields in {language}.\n\nStructures:\n"
                       + json.dumps(entries, ensure_ascii=False, indent=1),
        }],
    }


def ask_claude(request, client=None):
    if client is None:
        import anthropic                   # only needed for a real call
        client = anthropic.Anthropic()
    response = client.beta.messages.create(
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",               # retry on another model if this one declines
        **request,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to explain these structures")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("explanation was cut off; too many structures in one file")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def explain_file(src_path, lang="zh", client=None, dry_run=False):
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    entries = describe(analyzer.build_scene(src), src)
    key = fingerprint(entries, lang)
    try:
        with open(cache_path(src_path), encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("key") == key:
            return cached["result"]
    except (OSError, ValueError):
        pass
    request = build_request(entries, lang)
    if dry_run:
        print(json.dumps(request, ensure_ascii=False, indent=1))
        return None
    result = ask_claude(request, client)
    tmp = cache_path(src_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"key": key, "result": result}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, cache_path(src_path))      # Blender never sees a half-written file
    return result


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    path = os.path.abspath(argv[1] if len(argv) > 1 else "demo.py")
    lang = argv[argv.index("--lang") + 1] if "--lang" in argv else "zh"
    result = explain_file(path, lang, dry_run="--dry-run" in argv)
    if result is None:
        return
    for st in result["structures"]:
        flag = "" if st["matches_code"] else "   [! code names disagree with the geometry]"
        print(f"\n#{st['id']} {st['shape_name']}  ({st['concept']}){flag}\n   {st['explanation']}")
    for rel in result["relations"]:
        print(f"\n= {' / '.join(rel['members'])}\n   {rel['insight']}")


if __name__ == "__main__":
    main(sys.argv)
