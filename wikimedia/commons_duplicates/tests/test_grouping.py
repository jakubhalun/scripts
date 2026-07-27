"""Tests for SHA-1 grouping, the four metadata levels and cross-level de-duplication."""

from __future__ import annotations

import unittest

import commons_duplicate_finder as finder
from tests.support import make_file, photo_metadata


def levels(groups: list[finder.MatchGroup]) -> list[str]:
    return [group.level for group in groups]


def titles(group: finder.MatchGroup) -> set[str]:
    return {item.title for item in group.files}


class Sha1GroupingTests(unittest.TestCase):
    def test_groups_files_sharing_a_hash(self) -> None:
        first = make_file("File:A.jpg", sha1="abc")
        second = make_file("File:B.jpg", sha1="abc")
        other = make_file("File:C.jpg", sha1="def")
        groups = finder.group_by_sha1([first, second, other])
        self.assertEqual(len(groups), 1)
        self.assertEqual({item.title for item in groups[0]}, {"File:A.jpg", "File:B.jpg"})

    def test_ignores_files_without_a_hash(self) -> None:
        self.assertEqual(finder.group_by_sha1([make_file("File:A.jpg"), make_file("File:B.jpg")]), [])

    def test_binary_duplicates_are_reported_first_and_separately(self) -> None:
        metadata = photo_metadata()
        first = make_file("File:A.jpg", metadata, sha1="abc")
        second = make_file("File:B.jpg", metadata, sha1="abc")
        groups = finder.build_groups([first, second])
        self.assertEqual(levels(groups), ["sha1"])
        self.assertEqual(groups[0].classification, finder.CLASSIFICATION_EXACT)


class LevelOneTests(unittest.TestCase):
    def test_groups_files_whose_full_capture_metadata_matches(self) -> None:
        metadata = photo_metadata()
        groups = finder.build_groups([make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)])
        self.assertEqual(levels(groups), ["level1"])
        self.assertEqual(groups[0].classification, finder.CLASSIFICATION_VERY_STRONG)

    def test_different_dimensions_still_group_together(self) -> None:
        metadata = photo_metadata()
        first = make_file("File:A.jpg", metadata, width=4000, height=3000)
        second = make_file("File:B.jpg", metadata, width=800, height=600)
        groups = finder.build_groups([first, second])
        self.assertEqual(levels(groups), ["level1"])

    def test_a_different_lens_prevents_a_level_one_group(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(lens="EF 24-70mm"))
        second = make_file("File:B.jpg", photo_metadata(lens="EF 70-200mm"))
        self.assertNotIn("level1", levels(finder.build_groups([first, second])))


class LevelTwoTests(unittest.TestCase):
    def test_lens_missing_from_one_file_still_groups(self) -> None:
        first = make_file("File:A.jpg", photo_metadata())
        second = make_file("File:B.jpg", photo_metadata(lens=None))
        groups = finder.build_groups([first, second])
        self.assertEqual(levels(groups), ["level2"])
        self.assertEqual(groups[0].classification, finder.CLASSIFICATION_STRONG)

    def test_a_different_iso_prevents_a_level_two_group(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(lens=None, iso="200"))
        second = make_file("File:B.jpg", photo_metadata(lens=None, iso="800"))
        self.assertNotIn("level2", levels(finder.build_groups([first, second])))


class LevelThreeTests(unittest.TestCase):
    def test_same_timestamp_and_camera_is_reported_as_a_possible_burst(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(lens=None, iso="200", aperture="5.6"))
        second = make_file("File:B.jpg", photo_metadata(lens=None, iso="800", aperture="2.8"))
        groups = finder.build_groups([first, second], include_series=True)
        self.assertEqual(levels(groups), ["level3"])
        self.assertEqual(groups[0].classification, finder.CLASSIFICATION_BURST)

    def test_burst_candidates_are_withheld_unless_requested(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(lens=None, iso="200", aperture="5.6"))
        second = make_file("File:B.jpg", photo_metadata(lens=None, iso="800", aperture="2.8"))
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_a_different_camera_prevents_grouping(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(model="EOS 5D"))
        second = make_file("File:B.jpg", photo_metadata(model="EOS 7D"))
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_the_weakest_level_three_groups_are_labelled_weak(self) -> None:
        first = make_file(
            "File:A.jpg",
            photo_metadata(lens=None, exposure="1/125", aperture="5.6", iso="200", focal="50"),
        )
        second = make_file(
            "File:B.jpg",
            photo_metadata(lens=None, exposure="1/1000", aperture="2.8", iso="800", focal="200"),
        )
        groups = finder.build_groups([first, second], include_series=True)
        self.assertEqual(groups[0].classification, finder.CLASSIFICATION_WEAK)
        self.assertLess(groups[0].evidence.score, finder.WEAK_MATCH_SCORE_THRESHOLD)


class LevelFourTests(unittest.TestCase):
    def test_timestamps_one_second_apart_are_chained(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:00:01"))
        groups = finder.build_groups([first, second], include_series=True)
        self.assertEqual(levels(groups), ["level4"])
        self.assertEqual(groups[0].classification, finder.CLASSIFICATION_RELATED)

    def test_related_frames_are_withheld_unless_requested(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:00:01"))
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_timestamps_five_seconds_apart_are_not_chained_by_default(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:00:05"))
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_the_tolerance_is_configurable(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:00:05"))
        groups = finder.build_groups([first, second], near_timestamp_seconds=5, include_series=True)
        self.assertEqual(levels(groups), ["level4"])

    def test_differing_exposure_metadata_prevents_chaining(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00", iso="200"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:00:01", iso="800"))
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_a_chain_does_not_extend_across_a_large_gap(self) -> None:
        stamps = ["10:00:00", "10:00:01", "10:00:30", "10:00:31"]
        files = [
            make_file(f"File:{index}.jpg", photo_metadata(timestamp=f"2020:06:01 {stamp}"))
            for index, stamp in enumerate(stamps)
        ]
        groups = finder.build_groups(files, include_series=True)
        self.assertEqual(levels(groups), ["level4", "level4"])
        self.assertEqual(titles(groups[0]), {"File:0.jpg", "File:1.jpg"})
        self.assertEqual(titles(groups[1]), {"File:2.jpg", "File:3.jpg"})


class GroupingGuardTests(unittest.TestCase):
    def test_files_without_a_capture_timestamp_are_never_grouped(self) -> None:
        first = make_file("File:A.jpg", {"Make": "Canon", "Model": "EOS 5D"})
        second = make_file("File:B.jpg", {"Make": "Canon", "Model": "EOS 5D"})
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_files_without_any_exif_are_never_grouped(self) -> None:
        self.assertEqual(finder.build_groups([make_file("File:A.jpg"), make_file("File:B.jpg")]), [])

    def test_a_single_file_is_never_a_group(self) -> None:
        self.assertEqual(finder.build_groups([make_file("File:A.jpg", photo_metadata())]), [])

    def test_unusually_large_groups_carry_a_warning_note(self) -> None:
        metadata = photo_metadata()
        files = [make_file(f"File:{index}.jpg", metadata) for index in range(finder.LARGE_GROUP_THRESHOLD)]
        groups = finder.build_groups(files)
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].notes)
        self.assertIn("placeholder", groups[0].notes[0])


class SourceImageIdentifierTests(unittest.TestCase):
    """The XMP source identifier is the strongest signal short of an identical hash."""

    def test_two_exports_of_one_photograph_are_grouped(self) -> None:
        identifier = "3FF35750EEBA0FD54782748D"
        first = make_file(
            "File:Office buildings.jpg",
            photo_metadata(timestamp="2026:03:03 10:53:29", OriginalDocumentID=identifier),
        )
        second = make_file(
            "File:Office buildings..jpg",
            photo_metadata(timestamp="2026:03:03 10:53:29", OriginalDocumentID=identifier),
        )
        groups = finder.build_groups([first, second])
        self.assertEqual(levels(groups), ["source"])
        self.assertEqual(groups[0].classification, finder.CLASSIFICATION_SAME_SOURCE)

    def test_it_wins_over_the_metadata_levels(self) -> None:
        metadata = photo_metadata(OriginalDocumentID="SHARED")
        groups = finder.build_groups(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        self.assertEqual(levels(groups), ["source"])

    def test_it_groups_across_differing_capture_metadata(self) -> None:
        first = make_file(
            "File:A.jpg", photo_metadata(timestamp="2026:03:03 10:53:29", OriginalDocumentID="X")
        )
        second = make_file(
            "File:B.jpg",
            photo_metadata(timestamp="2011:01:01 00:00:00", make="Nikon", model="D3", OriginalDocumentID="X"),
        )
        self.assertEqual(levels(finder.build_groups([first, second])), ["source"])

    def test_different_identifiers_are_not_grouped(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(OriginalDocumentID="A", timestamp="2020:01:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(OriginalDocumentID="B", timestamp="2021:01:01 10:00:00"))
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_an_identifier_shared_by_too_many_files_is_ignored(self) -> None:
        """Some phones write one constant ImageUniqueID into every file they produce."""
        files = [
            make_file(
                f"File:Phone {index}.jpg",
                photo_metadata(timestamp=f"2024:02:10 11:{index:02d}:00", OriginalDocumentID="CONSTANT"),
            )
            for index in range(finder.MAX_SHARED_IDENTIFIER_FILES + 1)
        ]
        self.assertEqual(finder.group_by_source_image_id(files), [])
        self.assertNotIn("source", levels(finder.build_groups(files)))

    def test_image_unique_id_is_never_treated_as_a_source_identifier(self) -> None:
        self.assertNotIn("ImageUniqueID", finder.FIELD_ALIASES["source_image_id"])
        normalized = finder.normalize_metadata({"ImageUniqueID": "G12LLKA01VM G12LLKL01GM"})
        self.assertIsNone(normalized.comparison_key("source_image_id"))


class BrightnessConflictTests(unittest.TestCase):
    """Metered brightness is a measurement, so it separates frames that settings cannot."""

    def test_differing_brightness_marks_a_group_as_a_series(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(BrightnessValue="21874/2560"))
        second = make_file("File:B.jpg", photo_metadata(BrightnessValue="22184/2560"))
        self.assertTrue(finder.has_brightness_conflict([first, second]))
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_such_a_group_is_shown_with_a_note_when_series_are_requested(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(BrightnessValue="21874/2560"))
        second = make_file("File:B.jpg", photo_metadata(BrightnessValue="22184/2560"))
        groups = finder.build_groups([first, second], include_series=True)
        self.assertEqual(levels(groups), ["level1"])
        self.assertTrue(any("different frames" in note for note in groups[0].notes))

    def test_identical_brightness_keeps_the_group(self) -> None:
        metadata = photo_metadata(BrightnessValue="21874/2560")
        groups = finder.build_groups(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        self.assertEqual(levels(groups), ["level1"])

    def test_a_missing_value_proves_nothing_and_never_splits(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(BrightnessValue="21874/2560"))
        second = make_file("File:B.jpg", photo_metadata())
        self.assertFalse(finder.has_brightness_conflict([first, second]))
        self.assertEqual(levels(finder.build_groups([first, second])), ["level1"])

    def test_binary_duplicates_are_reported_whatever_the_brightness_says(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(BrightnessValue="21874/2560"), sha1="same")
        second = make_file("File:B.jpg", photo_metadata(BrightnessValue="22184/2560"), sha1="same")
        self.assertEqual(levels(finder.build_groups([first, second])), ["sha1"])

    def test_a_source_identifier_group_is_still_split_by_brightness(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(OriginalDocumentID="X", BrightnessValue="21874/2560"))
        second = make_file("File:B.jpg", photo_metadata(OriginalDocumentID="X", BrightnessValue="22184/2560"))
        self.assertEqual(finder.build_groups([first, second]), [])


class ContradictedTimestampTests(unittest.TestCase):
    """Reproduces a real false positive: editing software rewriting DateTimeOriginal.

    Five unrelated photographs all carried DateTimeOriginal 2025:08:29 12:24:06 with
    identical exposure settings, so they formed one 'Strong metadata match' group. Each
    file's own DateTimeDigitized showed a different real capture date.
    """

    SHARED = "2025:08:29 12:24:06"

    def unrelated_photographs(self) -> list[finder.CommonsFile]:
        digitized = [
            "2025:09:27 17:30:51",
            "2025:09:27 17:31:46",
            "2025:08:29 12:24:06",
            "2025:08:30 15:17:57",
            "2025:08:30 15:18:19",
        ]
        return [
            make_file(
                f"File:Subject {index}.jpg",
                photo_metadata(
                    timestamp=self.SHARED,
                    make="SONY",
                    model="ILCE-6000",
                    lens=None,
                    exposure="1/60",
                    aperture="50/10",
                    iso="1250",
                    focal="160/10",
                    DateTimeDigitized=value,
                ),
            )
            for index, value in enumerate(digitized)
        ]

    def test_unrelated_photographs_are_no_longer_grouped(self) -> None:
        self.assertEqual(finder.build_groups(self.unrelated_photographs()), [])

    def test_they_would_have_been_grouped_without_the_digitized_check(self) -> None:
        files = self.unrelated_photographs()
        for item in files:
            item.normalized.capture_status = finder.CAPTURE_NO_SECONDARY
        groups = finder.build_groups(files)
        self.assertEqual(levels(groups), ["level2"])
        self.assertEqual(len(groups[0].files), 5)

    def test_the_same_photo_exported_by_two_tools_still_groups(self) -> None:
        capture = "2025:08:19 12:35:36"
        lightroom = make_file("File:View A.jpg", photo_metadata(timestamp=capture, DateTimeDigitized=capture))
        elements = make_file(
            "File:View A duplicate.jpg",
            photo_metadata(timestamp=capture, DateTimeDigitized="2025:08:19 14:35:36"),
        )
        gimp = make_file("File:View A third.jpg", photo_metadata(timestamp=capture))
        groups = finder.build_groups([lightroom, elements, gimp])
        self.assertEqual(levels(groups), ["level1"])
        self.assertEqual(len(groups[0].files), 3)

    def test_two_files_sharing_the_same_contradiction_still_group(self) -> None:
        metadata = photo_metadata(timestamp=self.SHARED, DateTimeDigitized="2025:09:27 17:30:51")
        groups = finder.build_groups(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        self.assertEqual(levels(groups), ["level1"])

    def test_the_group_carries_a_note_when_a_timestamp_is_contradicted(self) -> None:
        metadata = photo_metadata(timestamp=self.SHARED, DateTimeDigitized="2025:09:27 17:30:51")
        groups = finder.build_groups(
            [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        )
        self.assertTrue(any("contradicts" in note for note in groups[0].notes))

    def test_contradicted_timestamps_are_excluded_from_near_timestamp_chaining(self) -> None:
        first = make_file(
            "File:A.jpg",
            photo_metadata(timestamp="2020:06:01 10:00:00", DateTimeDigitized="2019:01:01 08:00:00"),
        )
        second = make_file(
            "File:B.jpg",
            photo_metadata(timestamp="2020:06:01 10:00:01", DateTimeDigitized="2018:01:01 08:00:00"),
        )
        self.assertEqual(finder.build_groups([first, second]), [])

    def test_binary_duplicates_are_still_reported_despite_a_contradiction(self) -> None:
        metadata = photo_metadata(timestamp=self.SHARED, DateTimeDigitized="2025:09:27 17:30:51")
        first = make_file("File:A.jpg", metadata, sha1="same")
        second = make_file("File:B.jpg", photo_metadata(timestamp=self.SHARED), sha1="same")
        self.assertEqual(levels(finder.build_groups([first, second])), ["sha1"])


class CrossLevelDeduplicationTests(unittest.TestCase):
    def test_the_same_pair_is_reported_only_at_its_strongest_level(self) -> None:
        metadata = photo_metadata()
        groups = finder.build_groups([make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].level, "level1")

    def test_a_weaker_level_is_reported_when_it_adds_a_new_file(self) -> None:
        strong = photo_metadata()
        weaker = photo_metadata(lens=None, iso="800")
        files = [
            make_file("File:A.jpg", strong),
            make_file("File:B.jpg", strong),
            make_file("File:C.jpg", weaker),
        ]
        groups = finder.build_groups(files, include_series=True)
        self.assertEqual(levels(groups), ["level1", "level3"])
        self.assertEqual(titles(groups[0]), {"File:A.jpg", "File:B.jpg"})
        self.assertEqual(titles(groups[1]), {"File:A.jpg", "File:B.jpg", "File:C.jpg"})

    def test_binary_duplicates_suppress_the_metadata_levels_for_the_same_pair(self) -> None:
        metadata = photo_metadata()
        first = make_file("File:A.jpg", metadata, sha1="abc")
        second = make_file("File:B.jpg", metadata, sha1="abc")
        groups = finder.build_groups([first, second])
        self.assertEqual(levels(groups), ["sha1"])


if __name__ == "__main__":
    unittest.main()
