#!/usr/bin/env python3

from __future__ import annotations

import json
import unittest
from pathlib import Path

from reference_oracle import EVALUATORS, evaluate_case


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    with (ROOT / relative).open(encoding="utf-8") as handle:
        return json.load(handle)


class CatalogContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.algorithms = load("specs/algorithm_catalog.json")
        cls.morphologies = load("specs/morphology_catalog.json")
        cls.invariants = load("specs/invariants.json")
        cls.state_machine = load("specs/state_machine.json")
        cls.structural = load("fixtures/structural_cases.json")
        cls.scenarios = load("fixtures/strategy_scenarios.json")
        cls.images = load("manifests/images.json")

    def test_algorithm_ids_unique_and_dependencies_resolve(self):
        items = self.algorithms["algorithms"]
        ids = [item["id"] for item in items]
        self.assertEqual(len(ids), len(set(ids)))
        known = set(ids)
        for item in items:
            self.assertTrue(item["source_lessons"])
            self.assertTrue(item["preconditions"])
            self.assertTrue(item["steps"])
            for dependency in item["depends_on"]:
                self.assertIn(dependency, known, (item["id"], dependency))

    def test_no_profit_guarantee(self):
        self.assertFalse(self.algorithms["profit_guarantee"])
        self.assertTrue(self.algorithms["default_research_only"])
        config = load("specs/config.example.json")
        self.assertFalse(config["research"]["profit_guarantee"])

    def test_source_lessons_are_in_range(self):
        for item in self.algorithms["algorithms"]:
            for lesson in item["source_lessons"]:
                self.assertGreaterEqual(lesson, 1)
                self.assertLessEqual(lesson, 108)
        for item in self.morphologies["objects"]:
            for lesson in item["source_lessons"]:
                self.assertGreaterEqual(lesson, 1)
                self.assertLessEqual(lesson, 108)

    def test_morphology_ids_unique(self):
        ids = [item["id"] for item in self.morphologies["objects"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_critical_invariants_present(self):
        rules = {item["id"]: item["rule"] for item in self.invariants["invariants"]}
        self.assertIn("equality is accepted", rules["INV-B3-002"])
        self.assertIn("equality is accepted", rules["INV-S3-002"])
        self.assertIn("never bi", rules["INV-ZS-002"])
        self.assertIn("exception never applies to bi", rules["INV-SEG-005"])
        self.assertIn("execution_time >= available_at", rules["INV-TIME-002"])

    def test_bi_transition_graph_exact(self):
        machine = self.state_machine["machines"]["bi_live"]
        edges = {(t["from"], t["to"]) for t in machine["transitions"]}
        forbidden = {
            ("UP_EXTENDING", "DOWN_EXTENDING"),
            ("UP_EXTENDING", "BOTTOM_FORMING"),
            ("DOWN_EXTENDING", "UP_EXTENDING"),
            ("DOWN_EXTENDING", "TOP_FORMING"),
        }
        self.assertTrue(edges.isdisjoint(forbidden))

    def test_all_structural_cases_have_evaluators_and_pass(self):
        seen = set()
        algorithm_ids = {item["id"] for item in self.algorithms["algorithms"]}
        for case in self.structural["cases"]:
            self.assertNotIn(case["id"], seen)
            seen.add(case["id"])
            self.assertIn(case["algorithm_id"], algorithm_ids)
            self.assertIn(case["category"], EVALUATORS)
            self.assertEqual(evaluate_case(case), case["expected"], case["id"])

    def test_strategy_scenarios_refer_to_known_algorithms(self):
        algorithm_ids = {item["id"] for item in self.algorithms["algorithms"]}
        scenario_ids = set()
        for case in self.scenarios["cases"]:
            self.assertIn(case["algorithm_id"], algorithm_ids)
            self.assertNotIn(case["id"], scenario_ids)
            scenario_ids.add(case["id"])
            self.assertTrue(case["source_lessons"])
            self.assertTrue(case["expected"])

    def test_image_manifest_complete(self):
        images = self.images["images"]
        self.assertEqual(self.images["count"], 24)
        self.assertEqual(len(images), 24)
        for item in images:
            path = ROOT / item["file"]
            self.assertTrue(path.is_file(), item["file"])
            self.assertGreater(path.stat().st_size, 0)
            self.assertTrue(item["source_url"].startswith("https://chanlun108.cn/uploads/"))

    def test_user_regression_image_present_but_not_course_evidence(self):
        case = self.images["user_cases"][0]
        self.assertTrue((ROOT / case["file"]).is_file())
        self.assertFalse(case["is_course_evidence"])


if __name__ == "__main__":
    unittest.main()
