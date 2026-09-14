import ast
from pathlib import Path
import unittest
from unittest.mock import Mock
from presentation import format_published_at


class InputBoundariesTests(unittest.TestCase):
    def test_dates_never_overflow(self):
        for value in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00", "nonsense", "", "2026-09-14T12:30:00Z"):
            self.assertIsInstance(format_published_at(value), str)

    def test_range_limits_before_allocation(self):
        # Execute the exact standalone function, avoiding provider/network imports.
        tree = ast.parse((Path(__file__).resolve().parents[1] / "watchers.py").read_text())
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "skip_range")
        forward = Mock(side_effect=lambda ids: {"requested": len(ids), "ids": ids})
        scope = {"skip_items": forward}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "watchers.py", "exec"), scope)
        call = scope["skip_range"]
        self.assertEqual(call(1, 200)["ids"], list(range(1, 201)))
        self.assertEqual(call(200, 1)["ids"], list(range(1, 201)))
        forward.reset_mock()
        scope["range"] = Mock(side_effect=AssertionError("must reject before allocation"))
        self.assertIn("error", call(1, 201))
        self.assertIn("error", call(1, 10**100))
        forward.assert_not_called()


if __name__ == "__main__":
    unittest.main()
