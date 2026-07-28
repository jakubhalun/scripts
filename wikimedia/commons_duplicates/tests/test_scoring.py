"""Tests for the transparent similarity score and its field breakdown."""

from __future__ import annotations

import unittest

import commons_duplicate_finder as finder
from tests.support import make_file, photo_metadata

LABELS = finder.FIELD_LABELS


class ScoreCompositionTests(unittest.TestCase):
    def test_identical_metadata_scores_every_available_field(self) -> None:
        metadata = photo_metadata(
            Orientation="1",
            SerialNumber="123456",
            OriginalDocumentID="ABCDEF0123456789",
            BrightnessValue="12324/2560",
        )
        evidence = finder.evaluate_group(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        self.assertEqual(evidence.score, sum(finder.FIELD_WEIGHTS.values()))
        self.assertEqual(evidence.differing, [])
        self.assertEqual(evidence.missing, [])

    def test_the_score_is_the_sum_of_the_matching_field_weights(self) -> None:
        metadata = photo_metadata()
        evidence = finder.evaluate_group(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        expected = sum(
            weight
            for name, weight in finder.FIELD_WEIGHTS.items()
            if LABELS[name] in evidence.matching
        )
        self.assertEqual(evidence.score, expected)

    def test_a_field_present_everywhere_but_different_counts_as_differing(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(iso="200"))
        second = make_file("File:B.jpg", photo_metadata(iso="800"))
        evidence = finder.evaluate_group([first, second])
        self.assertIn(LABELS["iso"], evidence.differing)
        self.assertNotIn(LABELS["iso"], evidence.matching)
        self.assertNotIn(LABELS["iso"], evidence.missing)

    def test_a_field_absent_from_one_file_counts_as_missing_not_differing(self) -> None:
        first = make_file("File:A.jpg", photo_metadata())
        second = make_file("File:B.jpg", photo_metadata(lens=None))
        evidence = finder.evaluate_group([first, second])
        self.assertIn(LABELS["lens"], evidence.missing)
        self.assertNotIn(LABELS["lens"], evidence.differing)

    def test_a_field_absent_everywhere_counts_as_missing(self) -> None:
        metadata = photo_metadata()
        evidence = finder.evaluate_group(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        self.assertIn(LABELS["serial"], evidence.missing)
        self.assertIn(LABELS["orientation"], evidence.missing)

    def test_missing_fields_never_add_points(self) -> None:
        first = make_file("File:A.jpg", photo_metadata())
        second = make_file("File:B.jpg", photo_metadata(lens=None))
        with_lens = finder.evaluate_group([first, make_file("File:C.jpg", photo_metadata())])
        without_lens = finder.evaluate_group([first, second])
        self.assertEqual(with_lens.score - without_lens.score, finder.FIELD_WEIGHTS["lens"])

    def test_every_field_lands_in_exactly_one_category(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(iso="200"))
        second = make_file("File:B.jpg", photo_metadata(iso="800", lens=None))
        evidence = finder.evaluate_group([first, second])
        reported = (
            evidence.matching + evidence.near_matching + evidence.differing + evidence.missing
        )
        self.assertEqual(len(reported), len(finder.FIELD_WEIGHTS))
        self.assertEqual(sorted(reported), sorted(LABELS[name] for name in finder.FIELD_WEIGHTS))


class NearTimestampScoringTests(unittest.TestCase):
    def test_a_nearly_matching_timestamp_scores_half_its_weight(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:00:01"))
        evidence = finder.evaluate_group([first, second], near_timestamp_seconds=1)
        self.assertEqual(evidence.near_matching, [LABELS["capture_timestamp"]])
        self.assertNotIn(LABELS["capture_timestamp"], evidence.matching)
        identical = finder.evaluate_group(
            [first, make_file("File:C.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))]
        )
        self.assertEqual(identical.score - evidence.score, finder.FIELD_WEIGHTS["capture_timestamp"] // 2)

    def test_a_timestamp_outside_the_tolerance_simply_differs(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:05:00"))
        evidence = finder.evaluate_group([first, second], near_timestamp_seconds=1)
        self.assertIn(LABELS["capture_timestamp"], evidence.differing)
        self.assertEqual(evidence.near_matching, [])

    def test_only_the_timestamp_can_be_a_near_match(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(focal="50"))
        second = make_file("File:B.jpg", photo_metadata(focal="51"))
        evidence = finder.evaluate_group([first, second])
        self.assertEqual(evidence.near_matching, [])
        self.assertIn(LABELS["focal_length"], evidence.differing)


class GroupEvidenceIntegrationTests(unittest.TestCase):
    def test_reported_groups_carry_their_evidence(self) -> None:
        metadata = photo_metadata()
        groups = finder.build_groups(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        evidence = groups[0].evidence
        self.assertGreater(evidence.score, 0)
        self.assertIn(LABELS["capture_timestamp"], evidence.matching)
        self.assertIn(LABELS["camera"], evidence.matching)

    def test_weights_and_labels_describe_the_same_fields(self) -> None:
        self.assertEqual(set(finder.FIELD_WEIGHTS), set(finder.FIELD_LABELS))


if __name__ == "__main__":
    unittest.main()
