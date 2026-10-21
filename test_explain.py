"""
Tests for explain.py with a fake Claude client: no network, no cost.
Run:  python -m unittest test_explain -v
"""
import json
import os
import shutil
import tempfile
import textwrap
import unittest
from types import SimpleNamespace

import explain

SRC = textwrap.dedent("""
    def f(x):
        return x * x

    def pairs(n):
        c = 0
        for i in range(n):
            for j in range(i):
                c += 1
        return c

    def total(n):
        t = 0
        for i in range(n):
            t += i
        return t
""")


class FakeClient:
    """Records requests; answers with a fixed, obviously-fake explanation."""

    def __init__(self, stop_reason="end_turn"):
        self.calls = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        entries = json.loads(kw["messages"][0]["content"].split("Structures:\n", 1)[1])
        body = {"structures": [{"id": e["id"], "shape_name": "TEST", "concept": "TEST",
                                "explanation": "TEST", "matches_code": True} for e in entries],
                "relations": []}
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=json.dumps(body))])


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "prog.py")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SRC)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_claude_sees_geometry_and_relations(self):
        entries = explain.describe(explain.analyzer.build_scene(SRC), SRC)
        by_title = {e["title"]: e for e in entries}
        self.assertEqual(by_title["pairs(n)"]["count_law_in_N"], "(N² - N)/2")
        self.assertIn("total(n)", by_title["pairs(n)"]["same_shape_as"])
        self.assertIn("#", by_title["pairs(n)"]["picture"])

    def test_result_is_cached_and_reused(self):
        client = FakeClient()
        first = explain.explain_file(self.path, client=client)
        second = explain.explain_file(self.path, client=client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(explain.load_cached(self.path, SRC), first)

    def test_editing_the_code_invalidates_the_cache(self):
        explain.explain_file(self.path, client=FakeClient())
        edited = SRC.replace("range(i)", "range(n)")
        self.assertIsNone(explain.load_cached(self.path, edited))

    def test_request_uses_structured_output_and_fallback(self):
        client = FakeClient()
        explain.explain_file(self.path, client=client)
        kw = client.calls[0]
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(kw["fallbacks"], "default")

    def test_refusal_is_reported_not_cached(self):
        with self.assertRaises(RuntimeError):
            explain.explain_file(self.path, client=FakeClient(stop_reason="refusal"))
        self.assertFalse(os.path.exists(explain.cache_path(self.path)))


if __name__ == "__main__":
    unittest.main()
