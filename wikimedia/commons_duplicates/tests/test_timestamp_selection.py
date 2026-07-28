"""Tests for capture timestamp parsing and priority selection."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import commons_duplicate_finder as finder


class ParseExifDatetimeTests(unittest.TestCase):
    def test_parses_the_standard_exif_spelling(self) -> None:
        self.assertEqual(
            finder.parse_exif_datetime("2019:05:04 12:00:01"), datetime(2019, 5, 4, 12, 0, 1)
        )

    def test_parses_dashed_and_iso_like_spellings(self) -> None:
        expected = datetime(2019, 5, 4, 12, 0, 1)
        self.assertEqual(finder.parse_exif_datetime("2019-05-04 12:00:01"), expected)
        self.assertEqual(finder.parse_exif_datetime("2019-05-04T12:00:01"), expected)

    def test_drops_subseconds_and_timezone_suffixes(self) -> None:
        expected = datetime(2019, 5, 4, 12, 0, 1)
        self.assertEqual(finder.parse_exif_datetime("2019:05:04 12:00:01.25"), expected)
        self.assertEqual(finder.parse_exif_datetime("2019-05-04T12:00:01+02:00"), expected)
        self.assertEqual(finder.parse_exif_datetime("2019-05-04T12:00:01Z"), expected)

    def test_rejects_placeholders_and_malformed_values(self) -> None:
        self.assertIsNone(finder.parse_exif_datetime("0000:00:00 00:00:00"))
        self.assertIsNone(finder.parse_exif_datetime(""))
        self.assertIsNone(finder.parse_exif_datetime("   "))
        self.assertIsNone(finder.parse_exif_datetime("last summer"))
        self.assertIsNone(finder.parse_exif_datetime("2019:13:45 99:99:99"))


class SelectCaptureTimestampTests(unittest.TestCase):
    def test_prefers_date_time_original(self) -> None:
        metadata = {
            "DateTimeOriginal": "2019:05:04 12:00:01",
            "DateTimeDigitized": "2020:01:01 00:00:00",
            "DateTime": "2021:01:01 00:00:00",
        }
        moment, source, raw = finder.select_capture_timestamp(metadata)
        self.assertEqual(moment, datetime(2019, 5, 4, 12, 0, 1))
        self.assertEqual(source, "DateTimeOriginal")
        self.assertEqual(raw, "2019:05:04 12:00:01")

    def test_falls_back_to_date_time_digitized(self) -> None:
        metadata = {"DateTimeDigitized": "2020:01:01 00:00:00", "DateTime": "2021:01:01 00:00:00"}
        _, source, _ = finder.select_capture_timestamp(metadata)
        self.assertEqual(source, "DateTimeDigitized")

    def test_falls_back_to_another_exif_capture_tag_before_generic_date_time(self) -> None:
        metadata = {"CreateDate": "2020:02:02 08:00:00", "DateTime": "2021:01:01 00:00:00"}
        _, source, _ = finder.select_capture_timestamp(metadata)
        self.assertEqual(source, "CreateDate")

    def test_uses_generic_date_time_as_the_last_resort(self) -> None:
        _, source, _ = finder.select_capture_timestamp({"DateTime": "2021:01:01 00:00:00"})
        self.assertEqual(source, "DateTime")

    def test_skips_a_stronger_tag_that_cannot_be_parsed(self) -> None:
        metadata = {
            "DateTimeOriginal": "0000:00:00 00:00:00",
            "DateTimeDigitized": "2020:01:01 00:00:00",
        }
        moment, source, _ = finder.select_capture_timestamp(metadata)
        self.assertEqual(source, "DateTimeDigitized")
        self.assertEqual(moment, datetime(2020, 1, 1, 0, 0, 0))

    def test_matches_tag_names_case_insensitively(self) -> None:
        _, source, _ = finder.select_capture_timestamp({"datetimeoriginal": "2019:05:04 12:00:01"})
        self.assertEqual(source, "DateTimeOriginal")

    def test_returns_none_when_no_capture_tag_is_usable(self) -> None:
        self.assertIsNone(finder.select_capture_timestamp({}))
        self.assertIsNone(finder.select_capture_timestamp({"DateTimeOriginal": "nonsense"}))

    def test_never_falls_back_to_the_commons_upload_timestamp(self) -> None:
        item = finder.CommonsFile(title="File:A.jpg", upload_timestamp="2022-03-04T05:06:07Z")
        item.normalized = finder.normalize_metadata(item.raw_metadata)
        self.assertIsNone(item.normalized.capture_timestamp)
        self.assertEqual(item.upload_timestamp, "2022-03-04T05:06:07Z")

    def test_upload_timestamp_tags_are_not_capture_candidates(self) -> None:
        self.assertNotIn("timestamp", [tag.lower() for tag in finder.CAPTURE_TIMESTAMP_TAGS])


class CaptureCorroborationTests(unittest.TestCase):
    """DateTimeDigitized is used to judge whether DateTimeOriginal can be trusted."""

    def test_an_identical_digitized_timestamp_corroborates_the_capture(self) -> None:
        moment = datetime(2025, 8, 29, 12, 24, 6)
        self.assertEqual(finder.classify_capture_timestamp(moment, moment), finder.CAPTURE_CORROBORATED)

    def test_a_whole_hour_difference_is_only_a_timezone_disagreement(self) -> None:
        capture = datetime(2025, 8, 19, 12, 35, 36)
        for hours in (1, 2, -2, 26):
            with self.subTest(hours=hours):
                digitized = capture + timedelta(hours=hours)
                self.assertEqual(
                    finder.classify_capture_timestamp(capture, digitized),
                    finder.CAPTURE_TIMEZONE_OFFSET,
                )

    def test_a_different_day_contradicts_the_capture_timestamp(self) -> None:
        capture = datetime(2025, 8, 29, 12, 24, 6)
        for digitized in (
            datetime(2025, 9, 27, 17, 30, 51),
            datetime(2025, 8, 30, 15, 17, 57),
            datetime(2025, 8, 29, 12, 24, 30),
        ):
            with self.subTest(digitized=digitized):
                self.assertEqual(
                    finder.classify_capture_timestamp(capture, digitized), finder.CAPTURE_CONTRADICTED
                )

    def test_an_offset_beyond_any_real_timezone_still_contradicts(self) -> None:
        capture = datetime(2025, 8, 29, 12, 24, 6)
        self.assertEqual(
            finder.classify_capture_timestamp(capture, capture + timedelta(hours=48)),
            finder.CAPTURE_CONTRADICTED,
        )

    def test_a_missing_digitized_timestamp_leaves_the_capture_unchecked(self) -> None:
        self.assertEqual(
            finder.classify_capture_timestamp(datetime(2025, 8, 29, 12, 24, 6), None),
            finder.CAPTURE_NO_SECONDARY,
        )

    def test_normalize_metadata_records_the_status_and_the_digitized_value(self) -> None:
        normalized = finder.normalize_metadata(
            {"DateTimeOriginal": "2025:08:29 12:24:06", "DateTimeDigitized": "2025:09:27 17:30:51"}
        )
        self.assertEqual(normalized.capture_status, finder.CAPTURE_CONTRADICTED)
        self.assertTrue(normalized.has_contradicted_capture)
        self.assertEqual(normalized.digitized_timestamp, datetime(2025, 9, 27, 17, 30, 51))
        self.assertEqual(normalized.digitized_raw, "2025:09:27 17:30:51")

    def test_a_contradicted_capture_carries_the_digitized_value_in_its_group_key(self) -> None:
        shared = "2025:08:29 12:24:06"
        first = finder.normalize_metadata({"DateTimeOriginal": shared, "DateTimeDigitized": "2025:09:27 17:30:51"})
        second = finder.normalize_metadata({"DateTimeOriginal": shared, "DateTimeDigitized": "2025:08:30 15:17:57"})
        clean = finder.normalize_metadata({"DateTimeOriginal": shared, "DateTimeDigitized": shared})
        keys = {item.comparison_key("capture_timestamp") for item in (first, second, clean)}
        self.assertEqual(len(keys), 3)

    def test_a_timezone_offset_does_not_change_the_group_key(self) -> None:
        capture = "2025:08:19 12:35:36"
        lightroom = finder.normalize_metadata({"DateTimeOriginal": capture, "DateTimeDigitized": capture})
        elements = finder.normalize_metadata(
            {"DateTimeOriginal": capture, "DateTimeDigitized": "2025:08:19 14:35:36"}
        )
        gimp = finder.normalize_metadata({"DateTimeOriginal": capture})
        self.assertEqual(
            lightroom.comparison_key("capture_timestamp"), elements.comparison_key("capture_timestamp")
        )
        self.assertEqual(lightroom.comparison_key("capture_timestamp"), gimp.comparison_key("capture_timestamp"))

    def test_a_capture_taken_from_digitized_is_not_checked_against_itself(self) -> None:
        normalized = finder.normalize_metadata({"DateTimeDigitized": "2025:08:29 12:24:06"})
        self.assertEqual(normalized.capture_source, "DateTimeDigitized")
        self.assertFalse(normalized.has_contradicted_capture)


if __name__ == "__main__":
    unittest.main()
