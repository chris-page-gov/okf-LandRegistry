from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TraceabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogue = json.loads(
            (ROOT / "personas" / "personas-and-user-stories.json").read_text()
        )
        cls.questions = json.loads((ROOT / "evaluation" / "questions.json").read_text())
        cls.journeys = json.loads((ROOT / "evaluation" / "journeys.json").read_text())
        cls.sources = json.loads(
            (ROOT / "research" / "source-family-inventory.json").read_text()
        )

    def test_candidate_suite_has_declared_size(self) -> None:
        self.assertEqual(9, len(self.catalogue["personas"]))
        self.assertEqual(12, len(self.catalogue["stories"]))
        self.assertEqual(24, len(self.questions["questions"]))
        self.assertEqual(24, self.questions["question_count"])

    def test_question_story_persona_references_resolve(self) -> None:
        persona_ids = {item["id"] for item in self.catalogue["personas"]}
        story_ids = {item["id"] for item in self.catalogue["stories"]}
        hard_failure_ids = {item["id"] for item in self.questions["hard_failures"]}
        for question in self.questions["questions"]:
            self.assertTrue(set(question["persona_ids"]) <= persona_ids, question["id"])
            self.assertTrue(set(question["story_ids"]) <= story_ids, question["id"])
            self.assertTrue(
                set(question["hard_failure_ids"]) <= hard_failure_ids, question["id"]
            )

    def test_every_question_and_story_has_a_journey(self) -> None:
        journey_story_ids: set[str] = set()
        for journey in self.journeys["journeys"]:
            journey_story_ids.update(journey.get("story_ids", []))
        expected_questions = {item["id"] for item in self.questions["questions"]}
        expected_stories = {item["id"] for item in self.catalogue["stories"]}
        self.assertEqual(expected_stories, journey_story_ids)
        questions_exercised_by_those_stories = {
            question_id
            for story in self.catalogue["stories"]
            if story["id"] in journey_story_ids
            for question_id in story["question_ids"]
        }
        self.assertEqual(expected_questions, questions_exercised_by_those_stories)

    def test_rubric_totals_one_hundred(self) -> None:
        self.assertEqual(
            100, sum(section["points"] for section in self.questions["rubric"].values())
        )


if __name__ == "__main__":
    unittest.main()
