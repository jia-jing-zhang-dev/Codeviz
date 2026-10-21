"""
Tests for analyzer.py.  Run:  python -m unittest test_analyzer -v

Each test states a claim about the rules, not about a named concept:
the expected cells are written out as coordinate sets, never as "square" etc.
"""
import textwrap
import unittest

from analyzer import build_scene


def scene(src):
    sc = build_scene(textwrap.dedent(src))
    return {st["title"]: st for st in sc["structures"]}, sc


def cells(st):
    d = len(st["axes"])
    return {tuple(c[:d]) for c in st["cells"]}


def region_counts(st):
    d = len(st["axes"])
    out = {}
    for c in st["cells"]:
        name = st["regions"][c[d]]
        out[name] = out.get(name, 0) + 1
    return out


class Rules(unittest.TestCase):
    def test_product_of_a_quantity_with_itself_spans_two_equal_directions(self):
        s, _ = scene("""
            #viz x=4
            def f(x):
                return x * x
        """)
        self.assertEqual(cells(s["f(x)"]), {(i, j) for i in range(4) for j in range(4)})

    def test_three_factors_span_three_directions(self):
        s, _ = scene("""
            #viz a=2, b=3, c=4
            def g(a, b, c):
                return a * b * c
        """)
        st = s["g(a, b, c)"]
        self.assertEqual(len(st["axes"]), 3)
        self.assertEqual(sorted(max(c[k] for c in cells(st)) + 1 for k in range(3)), [2, 3, 4])
        self.assertEqual(st["law"], "N³")

    def test_dependent_inner_bound_fills_only_below_the_diagonal(self):
        s, _ = scene("""
            #viz n=5
            def p(n):
                c = 0
                for i in range(n):
                    for j in range(i):
                        c += 1
                return c
        """)
        # i=0 has no j, so after translating to the origin: rows of length 1..4
        self.assertEqual(cells(s["p(n)"]), {(i, j) for i in range(4) for j in range(i + 1)})
        self.assertEqual(s["p(n)"]["law"], "(N² - N)/2")

    def test_condition_carves_executed_points_only(self):
        s, _ = scene("""
            #viz n=4
            def q(n):
                c = 0
                for i in range(n):
                    for j in range(n):
                        if i != j:
                            c += 1
                return c
        """)
        self.assertEqual(cells(s["q(n)"]), {(i, j) for i in range(4) for j in range(4) if i != j})
        self.assertEqual(s["q(n)"]["law"], "N² - N")

    def test_circle_condition_gives_lattice_points_and_no_polynomial_law(self):
        s, _ = scene("""
            #viz r=3
            def h(r):
                k = 0
                for x in range(2 * r + 1):
                    for y in range(2 * r + 1):
                        if (x - r) ** 2 + (y - r) ** 2 <= r * r:
                            k += 1
                return k
        """)
        expected = {(x, y) for x in range(7) for y in range(7) if (x - 3) ** 2 + (y - 3) ** 2 <= 9}
        self.assertEqual(cells(s["h(r)"]), expected)
        self.assertIsNone(s["h(r)"]["law"])        # grows like r², which is neither a polynomial fit nor log

    def test_triple_dependent_loops(self):
        s, _ = scene("""
            def t(n):
                c = 0
                for i in range(n):
                    for j in range(i):
                        for k in range(j):
                            c += 1
                return c
        """)
        self.assertEqual(len(s["t(n)"]["axes"]), 3)
        self.assertEqual(s["t(n)"]["law"], "(N³ - 3N² + 2N)/6")

    def test_sum_of_a_product_splits_into_provenance_pieces(self):
        s, _ = scene("""
            #viz x=4
            def e(x):
                return (x + 1) * (x + 1)
        """)
        self.assertEqual(region_counts(s["e(x)"]), {"x·x": 16, "1·x": 8, "1·1": 1})
        self.assertEqual(s["e(x)"]["law"], "N² + 2N + 1")

    def test_nested_list_has_one_direction_per_level(self):
        s, _ = scene("""
            #viz n=3
            t = [[0] * n for _ in range(n)]
        """)
        self.assertEqual(cells(s["t"]), {(i, j) for i in range(3) for j in range(3)})


class Similarity(unittest.TestCase):
    SRC = """
        def f(x):
            return x * x

        def pairs(n):
            count = 0
            for i in range(n):
                for j in range(i):
                    count += 1
            return count

        def running_total(n):
            total = 0
            for i in range(n):
                total += i
            return total

        closed = n * (n - 1) // 2

        table = [[0] * n for _ in range(n)]
    """

    def setUp(self):
        self.s, _ = scene(self.SRC)

    def test_loop_and_accumulation_are_same_shape(self):
        self.assertIn("running_total(n)", self.s["pairs(n)"]["same_shape_as"])
        self.assertIn("pairs(n)", self.s["running_total(n)"]["same_shape_as"])

    def test_arithmetic_and_data_structure_are_same_shape(self):
        self.assertIn("table", self.s["f(x)"]["same_shape_as"])

    def test_formula_has_same_count_as_loops(self):
        self.assertIn("pairs(n)", self.s["closed"]["same_law_as"])
        self.assertIn("running_total(n)", self.s["closed"]["same_law_as"])

    def test_different_structures_are_not_merged(self):
        self.assertNotIn("pairs(n)", self.s["f(x)"]["same_shape_as"])
        self.assertNotIn("table", self.s["pairs(n)"]["same_shape_as"])
        self.assertNotIn("closed", self.s["pairs(n)"]["same_shape_as"])   # same count, different shape

    def test_mirror_image_counts_as_same_shape(self):
        s, _ = scene("""
            def lower(n):
                c = 0
                for i in range(n):
                    for j in range(i):
                        c += 1
                return c

            def upper(n):
                c = 0
                for i in range(n):
                    for j in range(i + 1, n):
                        c += 1
                return c
        """)
        self.assertIn("upper(n)", s["lower(n)"]["same_shape_as"])


class NamesDoNotMatter(unittest.TestCase):
    """The core principle: the result comes from structure, never from identifiers."""

    def test_misleading_names_do_not_change_the_shape(self):
        honest, _ = scene("""
            #viz n=5
            def pairs(n):
                c = 0
                for i in range(n):
                    for j in range(i):
                        c += 1
                return c
        """)
        lying, _ = scene("""
            #viz side=5
            def square_area(side):
                circle = 0
                for width in range(side):
                    for height in range(width):
                        circle += 1
                return circle
        """)
        self.assertEqual(cells(honest["pairs(n)"]), cells(lying["square_area(side)"]))
        self.assertEqual(honest["pairs(n)"]["law"], lying["square_area(side)"]["law"])


class Mutation(unittest.TestCase):
    """Found by the held-out test: item assignment and append must really execute."""

    def test_swaps_depend_on_the_actual_data(self):
        s, _ = scene("""
            #viz n=7
            def bub(n):
                arr = [2, 1, 3, 4, 5, 6, 7]
                swaps = 0
                for i in range(n - 1):
                    for j in range(n - 1 - i):
                        if arr[j] > arr[j + 1]:
                            arr[j], arr[j + 1] = arr[j + 1], arr[j]
                            swaps += 1
                return swaps
        """)
        self.assertEqual(s["bub(n)"]["count"], 1)

    def test_append_adds_slots(self):
        s, _ = scene("""
            #viz n=4
            def rows(n):
                out = []
                for i in range(n):
                    out.append([0] * (i + 1))
                return out
        """)
        self.assertEqual(cells(s["rows(n)"]), {(i, j) for i in range(4) for j in range(i + 1)})


class HeldOut(unittest.TestCase):
    """heldout.py was written by an agent that never saw analyzer.py."""

    @classmethod
    def setUpClass(cls):
        import os
        with open(os.path.join(os.path.dirname(__file__), "heldout.py"), encoding="utf-8") as f:
            cls.s, _ = scene(f.read())

    def test_three_sorts_and_pair_counting_share_one_shape(self):
        group = set(self.s["count_pairs(n)"]["same_shape_as"])
        self.assertTrue({"bubble_sort(n)", "insertion_sort(n) loop body",
                         "selection_sort(n) loop body"} <= group)

    def test_pascal_rows_match_subarray_count(self):
        self.assertIn("count_subarrays(n)", self.s["pascals_triangle(n)"]["same_shape_as"])

    def test_matrix_multiply_work_is_cubic(self):
        self.assertEqual(self.s["matrix_multiply(n) loop body"]["law"], "N³")

    def test_sum_of_squares_law(self):
        self.assertEqual(self.s["sum_of_squares(n)"]["law"], "(2N³ + 3N² + N)/6")

    def test_halving_is_recognised_as_logarithmic(self):
        # rule: doubling N adds a steady amount -> c * log2 N (never told "binary search")
        self.assertEqual(self.s["binary_search(n, target)"]["law"], "≈ log₂N")

    def test_irregular_growth_is_not_forced_into_a_law(self):
        self.assertIsNone(self.s["sieve_of_eratosthenes(n)"]["law"])


class Recursion(unittest.TestCase):
    """R8: calls nest one level deeper; sibling calls lie end to end."""

    def test_single_self_call_is_a_line(self):
        s, _ = scene("""
            #viz n=5
            def fact(n):
                if n <= 1:
                    return 1
                return n * fact(n - 1)
        """)
        st = s["fact(n) calls"]
        self.assertEqual(len(st["axes"]), 1)
        self.assertEqual(st["law"], "N")

    def test_two_equal_self_calls_halve_evenly(self):
        s, _ = scene("""
            #viz n=4
            def h(n):
                if n == 0:
                    return 0
                return h(n - 1) + 1 + h(n - 1)
        """)
        # every row is fully covered: width 2^n, n + 1 rows
        self.assertEqual(cells(s["h(n) calls"]), {(x, d) for x in range(16) for d in range(5)})
        self.assertEqual(s["h(n)"]["law"], "≈ 2.002^N")

    def test_fibonacci_ratio_is_discovered_not_given(self):
        s, _ = scene("""
            def fib(n):
                if n <= 1:
                    return n
                return fib(n - 1) + fib(n - 2)
        """)
        self.assertEqual(s["fib(n)"]["law"], "≈ 1.618^N")

    def test_helpers_appear_inside_their_caller_not_alone(self):
        s, _ = scene("""
            def merge(a, b):
                out = []
                i = j = 0
                while i < len(a) and j < len(b):
                    if a[i] <= b[j]:
                        out.append(a[i])
                        i += 1
                    else:
                        out.append(b[j])
                        j += 1
                return out + a[i:] + b[j:]

            def msort(a):
                if len(a) <= 1:
                    return a
                m = len(a) // 2
                return merge(msort(a[:m]), msort(a[m:]))

            def run(n):
                return msort(list(range(n, 0, -1)))
        """)
        self.assertNotIn("merge(a, b)", s)
        self.assertNotIn("msort(a)", s)
        self.assertIn("merge", s["run(n) calls"]["regions"])
        self.assertEqual(s["run(n)"]["law"], "N")          # sorted list keeps all n items


class Robustness(unittest.TestCase):
    def test_syntax_error_is_raised_so_the_viewer_keeps_last_good_scene(self):
        with self.assertRaises(SyntaxError):
            build_scene("def f(x):\n    return x *\n")

    def test_infinite_loop_terminates(self):
        s, sc = scene("""
            def spin(n):
                c = 0
                while True:
                    c += 1
                return c
        """)
        self.assertIsInstance(sc["structures"], list)

    def test_unsupported_code_does_not_crash(self):
        s, sc = scene("""
            import os
            class A:
                pass
            d = {"k": 1}
            def f(x):
                y = [v for v in "abc"]
                return x - 1
        """)
        self.assertIn("f(x)", s)

    def test_runtime_error_in_one_statement_does_not_stop_the_rest(self):
        s, sc = scene("""
            bad = 1 // 0 + undefined_call()
            good = n * n
        """)
        self.assertIn("good", s)


if __name__ == "__main__":
    unittest.main()
