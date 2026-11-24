import random
import unittest
from pathlib import Path

from nesttidy.planner import collect_parent_paths


def legacy(targets):
    result = set()
    for target in targets:
        result.update(target.parents)
    return result


class ParentClosureTests(unittest.TestCase):
    def test_equivalent_shared_nested_and_distinct_parents(self):
        base = Path.cwd() / "fixture"
        rng = random.Random(7)
        targets = [base / ("Group" if i % 2 else "group") / str(rng.randrange(8))
                   / ("deep" if i % 3 else "..") / str(i) for i in range(200)]
        targets += [Path("relative") / "a", Path("other") / "b", base, base.parent]
        for paths in (targets, list(reversed(targets)), [], [base]):
            actual, expected = collect_parent_paths(paths), legacy(paths)
            self.assertEqual(actual, expected)
            self.assertEqual({str(p) for p in actual}, {str(p) for p in expected})

    def test_shared_parent_stops_ancestor_iteration(self):
        class Target:
            def __init__(self, parents):
                self.parents = iter(parents)
        class FailIfRead:
            def __iter__(self):
                yield Path("a/b")
                raise AssertionError("known parent closure must stop")
        self.assertEqual(collect_parent_paths([Target([Path("a/b"), Path("a"), Path(".")]),
                                               Target(FailIfRead())]),
                         {Path("a/b"), Path("a"), Path(".")})
