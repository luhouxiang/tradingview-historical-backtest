import json
import unittest
from pathlib import Path

from reference_oracle import classify_fractal, merge_inclusion, reduce_same_type


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "bi_cases.json"
REGIONS_PATH = ROOT / "manifests" / "image_regions.json"


def load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class FixtureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_json(FIXTURE_PATH)
        cls.cases = {case["id"]: case for case in cls.data["cases"]}

    def test_profile_and_price_policy(self):
        self.assertEqual(self.data["theory_profile"], "chanlun_108_strict")
        self.assertEqual(self.data["price_unit"], "integer_ticks")
        self.assertFalse(self.data["floating_epsilon_allowed"])

    def test_case_ids_are_unique(self):
        ids = [case["id"] for case in self.data["cases"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_source_lessons_are_within_108(self):
        for case in self.data["cases"]:
            self.assertGreaterEqual(case["source"]["lesson"], 1)
            self.assertLessEqual(case["source"]["lesson"], 108)

    def test_basic_top_fractal(self):
        case = self.cases["L62_F1_TOP_FRACTAL"]
        self.assertEqual(classify_fractal(case["input"]["processed_klines"]), "top")

    def test_basic_bottom_fractal(self):
        case = self.cases["L62_F2_BOTTOM_FRACTAL"]
        self.assertEqual(classify_fractal(case["input"]["processed_klines"]), "bottom")

    def test_three_k_complete_classification(self):
        case = self.cases["L62_F7_THREE_K_CLASSIFICATION"]
        expected = case["expected"]["classification"]
        for name, triples in case["input"]["samples"].items():
            klines = [
                {"id": f"{name}-{index}", "high": pair[0], "low": pair[1]}
                for index, pair in enumerate(triples)
            ]
            self.assertEqual(classify_fractal(klines), expected[name])

    def test_directional_inclusion(self):
        for case_id in ("L62_F6_UP_INCLUSION", "L62_F6_DOWN_INCLUSION"):
            case = self.cases[case_id]
            raw = {item["id"]: item for item in case["input"]["raw_klines"]}
            first_id, second_id = case["input"]["merge_pair"]
            actual = merge_inclusion(
                raw[first_id], raw[second_id], case["input"]["direction"]
            )
            self.assertEqual(actual, case["expected"]["merged_kline"])

    def test_same_type_reduction(self):
        expectations = {
            "L77_KEEP_LATER_HIGHER_TOP": "t2",
            "L77_KEEP_LATER_LOWER_BOTTOM": "b2",
            "L77_EQUAL_TOPS_KEEP_FIRST_ON_OPPOSITE": "t1",
        }
        for case_id, retained_id in expectations.items():
            case = self.cases[case_id]
            first, second = case["input"]["fractal_candidates"][:2]
            self.assertEqual(reduce_same_type(first, second)["id"], retained_id)

    def test_state_machine_matches_lesson_91_matrix(self):
        allowed = self.data["state_machine"]["allowed_transitions"]
        self.assertEqual(allowed["UP_EXTENDING"], ["UP_TOP_FRACTAL_BUILDING"])
        self.assertEqual(allowed["DOWN_EXTENDING"], ["DOWN_BOTTOM_FRACTAL_BUILDING"])
        self.assertEqual(
            set(allowed["UP_TOP_FRACTAL_BUILDING"]),
            {"UP_EXTENDING", "DOWN_EXTENDING"},
        )
        self.assertEqual(
            set(allowed["DOWN_BOTTOM_FRACTAL_BUILDING"]),
            {"DOWN_EXTENDING", "UP_EXTENDING"},
        )

    def test_image_manifest_files_exist(self):
        manifest = load_json(REGIONS_PATH)
        for image in manifest["images"]:
            self.assertTrue((ROOT / image["file"]).is_file(), image["file"])
            for region in image["regions"]:
                x, y, width, height = region["box"]
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)
                self.assertLessEqual(x + width, image["width"])
                self.assertLessEqual(y + height, image["height"])

    def test_integer_tick_prices(self):
        price_keys = {"high", "low", "price", "price_ticks", "difference_ticks"}

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in price_keys:
                        self.assertIsInstance(item, int, f"{key}={item!r}")
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(self.data["cases"])


if __name__ == "__main__":
    unittest.main()

