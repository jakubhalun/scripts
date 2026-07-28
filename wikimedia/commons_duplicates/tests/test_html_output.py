"""Tests for the standalone HTML report and the JSON export."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timezone

import commons_duplicate_finder as finder
from tests.support import make_file, photo_metadata

GENERATED_AT = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def build_report(files=None, groups=None, skipped=None, stats=None) -> str:
    return finder.render_html_report(
        target_user="Example User",
        requesting_user="Requesting User",
        stats=stats or finder.RunStats(total_files=2, files_with_metadata=2, api_requests=3),
        groups=groups if groups is not None else [],
        skipped=skipped or [],
        generated_at=GENERATED_AT,
    )


class ReportStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        metadata = photo_metadata()
        self.files = [
            make_file("File:A.jpg", metadata, sha1="abc123", width=4000, height=3000, size=2_500_000,
                      upload_timestamp="2022-03-04T05:06:07Z"),
            make_file("File:B.jpg", metadata, sha1="def456", width=800, height=600, size=90_000,
                      upload_timestamp="2022-03-05T05:06:07Z"),
        ]
        self.groups = finder.build_groups(self.files)
        self.document = build_report(self.files, self.groups)

    def test_reports_the_run_context(self) -> None:
        for expected in ("Example User", "Requesting User", "2024-05-01 12:00:00"):
            self.assertIn(expected, self.document)

    def test_lists_every_duplicate_section_even_when_empty(self) -> None:
        for level in ("sha1", "source", "level1", "level2"):
            self.assertIn(finder.SECTION_TITLES[level], self.document)
        self.assertIn("No groups found at this level.", self.document)
        self.assertIn("Errors and skipped files", self.document)

    def test_points_at_the_flag_when_no_series_groups_were_reported(self) -> None:
        self.assertIn("--include-series", self.document)

    def test_series_sections_appear_once_they_have_groups(self) -> None:
        first = make_file("File:A.jpg", photo_metadata(timestamp="2020:06:01 10:00:00"))
        second = make_file("File:B.jpg", photo_metadata(timestamp="2020:06:01 10:00:01"))
        groups = finder.build_groups([first, second], include_series=True)
        document = build_report([first, second], groups)
        self.assertIn(finder.SECTION_TITLES["level4"], document)

    def test_shows_the_classification_score_and_field_breakdown(self) -> None:
        group = self.groups[0]
        self.assertIn(group.classification, self.document)
        self.assertIn(f"<strong>{group.evidence.score}</strong>", self.document)
        self.assertIn("Matching fields:", self.document)
        self.assertIn(finder.FIELD_LABELS["capture_timestamp"], self.document)

    def test_shows_the_per_file_columns(self) -> None:
        for column in finder.TABLE_COLUMNS:
            self.assertIn(f"<th>{column}</th>", self.document)
        self.assertIn("2022-03-04T05:06:07Z", self.document)
        self.assertIn("4000 x 3000", self.document)
        self.assertIn("abc123", self.document)
        self.assertIn("2.4 MB", self.document)

    def test_links_to_the_commons_file_description_page(self) -> None:
        self.assertIn('href="https://commons.wikimedia.org/wiki/File:A.jpg"', self.document)

    def test_uses_cautious_wording_about_manual_review(self) -> None:
        lowered = self.document.lower()
        self.assertIn("candidates for manual review", lowered)
        self.assertIn("burst", lowered)
        self.assertIn("not proof", lowered)
        self.assertIn("do not use this report to nominate or tag files automatically", lowered)

    def test_states_that_it_is_read_only(self) -> None:
        self.assertIn("read-only", self.document.lower())
        self.assertIn("made no changes to Wikimedia Commons", self.document)

    def test_explains_the_scoring_weights(self) -> None:
        self.assertIn(f"{finder.FIELD_LABELS['capture_timestamp']} +40", self.document)


class SelfContainedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = build_report()

    def test_contains_no_images_or_thumbnails(self) -> None:
        lowered = self.document.lower()
        self.assertNotIn("<img", lowered)
        self.assertNotIn("<picture", lowered)
        self.assertNotIn("background-image", lowered)
        self.assertNotIn("upload.wikimedia.org", lowered)
        self.assertNotIn("/thumb/", lowered)

    def test_contains_no_scripts(self) -> None:
        self.assertNotIn("<script", self.document.lower())
        self.assertNotIn("onclick", self.document.lower())

    def test_embeds_its_own_css_and_loads_nothing_externally(self) -> None:
        self.assertIn("<style>", self.document)
        self.assertNotIn("<link", self.document.lower())
        for url in re.findall(r'(?:src|href)="([^"]+)"', self.document):
            self.assertTrue(
                url.startswith("https://commons.wikimedia.org/wiki/"),
                f"unexpected external reference: {url}",
            )

    def test_is_a_complete_html_document(self) -> None:
        self.assertTrue(self.document.startswith("<!DOCTYPE html>"))
        self.assertTrue(self.document.rstrip().endswith("</html>"))
        self.assertIn('<meta charset="utf-8">', self.document)


class EscapingTests(unittest.TestCase):
    def test_escapes_markup_in_file_titles(self) -> None:
        hostile = '<script>alert("x")</script> & co'
        files = [
            make_file(f"File:{hostile} 1.jpg", photo_metadata(), sha1="same"),
            make_file(f"File:{hostile} 2.jpg", photo_metadata(), sha1="same"),
        ]
        document = build_report(files, finder.build_groups(files))
        self.assertNotIn("<script>", document)
        self.assertIn("&lt;script&gt;", document)
        self.assertIn("&amp; co", document)

    def test_escapes_markup_in_metadata_values_and_skip_reasons(self) -> None:
        metadata = photo_metadata(make='<b>"Canon"</b>')
        files = [make_file("File:A.jpg", metadata), make_file("File:B.jpg", metadata)]
        skipped = [finder.SkippedFile("File:C.jpg", "<i>broken</i>")]
        document = build_report(files, finder.build_groups(files), skipped=skipped)
        self.assertNotIn("<b>", document)
        self.assertNotIn("<i>broken</i>", document)
        self.assertIn("&lt;i&gt;broken&lt;/i&gt;", document)

    def test_urls_with_special_characters_are_encoded(self) -> None:
        item = make_file('File:Zagłoba & "friends".jpg')
        self.assertIn("Zag%C5%82oba", item.page_url)
        self.assertIn("%22", item.page_url)
        document = build_report([item], [])
        self.assertNotIn('"friends"', document)


class SkippedFilesTests(unittest.TestCase):
    def test_lists_skipped_files_with_their_reason(self) -> None:
        skipped = [finder.SkippedFile("File:Gone.jpg", "page missing or deleted during the run")]
        document = build_report(skipped=skipped)
        self.assertIn("File:Gone.jpg", document)
        self.assertIn("page missing or deleted during the run", document)

    def test_says_so_when_nothing_was_skipped(self) -> None:
        self.assertIn("No files were skipped", build_report())


class FileSizeFormattingTests(unittest.TestCase):
    def test_renders_readable_sizes(self) -> None:
        self.assertEqual(finder.format_file_size(512), "512 B")
        self.assertEqual(finder.format_file_size(2048), "2.0 KB")
        self.assertEqual(finder.format_file_size(2_500_000), "2.4 MB")
        self.assertEqual(finder.format_file_size(None), "")


class JsonReportTests(unittest.TestCase):
    def setUp(self) -> None:
        metadata = photo_metadata()
        self.files = [make_file("File:A.jpg", metadata, sha1="abc"), make_file("File:B.jpg", metadata, sha1="def")]
        self.groups = finder.build_groups(self.files)
        self.payload = finder.build_json_report(
            "Example User",
            "Requesting User",
            finder.RunStats(total_files=2, files_with_metadata=2, api_requests=3),
            self.files,
            self.groups,
            [finder.SkippedFile("File:C.jpg", "no EXIF")],
            generated_at=GENERATED_AT,
        )

    def test_is_json_serializable(self) -> None:
        restored = json.loads(json.dumps(self.payload, ensure_ascii=False, default=str))
        self.assertEqual(restored["target_user"], "Example User")

    def test_contains_normalized_and_raw_metadata(self) -> None:
        entry = self.payload["files"][0]
        self.assertEqual(entry["normalized"]["capture_source"], "DateTimeOriginal")
        self.assertEqual(entry["normalized"]["fields"]["aperture"]["key"], "5.6")
        self.assertEqual(entry["raw_metadata"]["FNumber"], "5.6")

    def test_contains_groups_scores_classifications_and_errors(self) -> None:
        group = self.payload["groups"][0]
        self.assertEqual(group["level"], "level1")
        self.assertEqual(group["classification"], finder.CLASSIFICATION_VERY_STRONG)
        self.assertEqual(group["score"], self.groups[0].evidence.score)
        self.assertIn(finder.FIELD_LABELS["camera"], group["matching_fields"])
        self.assertEqual(group["files"], ["File:A.jpg", "File:B.jpg"])
        self.assertEqual(self.payload["skipped"], [{"title": "File:C.jpg", "reason": "no EXIF"}])

    def test_records_that_the_tool_is_read_only(self) -> None:
        self.assertTrue(self.payload["tool"]["read_only"])
        self.assertEqual(self.payload["scoring_weights"], finder.FIELD_WEIGHTS)


if __name__ == "__main__":
    unittest.main()
