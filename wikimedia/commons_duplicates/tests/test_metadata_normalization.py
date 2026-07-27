"""Tests for metadata flattening, alias lookup and value normalization."""

from __future__ import annotations

import unittest

import commons_duplicate_finder as finder


class FlattenMetadataTests(unittest.TestCase):
    def test_flattens_name_value_pairs(self) -> None:
        entries = [
            {"name": "Make", "value": "Canon"},
            {"name": "ISOSpeedRatings", "value": 200},
        ]
        self.assertEqual(finder.flatten_metadata(entries), {"Make": "Canon", "ISOSpeedRatings": "200"})

    def test_recurses_into_nested_name_value_lists(self) -> None:
        entries = [{"name": "Exif", "value": [{"name": "Model", "value": "EOS 5D"}]}]
        self.assertEqual(finder.flatten_metadata(entries), {"Model": "EOS 5D"})

    def test_joins_plain_lists_and_reads_language_dictionaries(self) -> None:
        entries = [
            {"name": "Keywords", "value": ["one", "two"]},
            {"name": "Description", "value": {"x-default": "a caption"}},
        ]
        self.assertEqual(
            finder.flatten_metadata(entries),
            {"Keywords": "one; two", "Description": "a caption"},
        )

    def test_ignores_malformed_input_without_raising(self) -> None:
        self.assertEqual(finder.flatten_metadata(None), {})
        self.assertEqual(finder.flatten_metadata("not a list"), {})
        self.assertEqual(finder.flatten_metadata([{"value": "no name"}, 42]), {})


class AliasLookupTests(unittest.TestCase):
    def test_matches_tag_names_case_insensitively(self) -> None:
        found = finder.lookup_raw({"make": "Nikon"}, finder.FIELD_ALIASES["make"])
        self.assertEqual(found, ("Make", "Nikon"))

    def test_prefers_the_first_listed_alias(self) -> None:
        metadata = {"PhotographicSensitivity": "400", "ISOSpeedRatings": "200"}
        self.assertEqual(finder.lookup_raw(metadata, finder.FIELD_ALIASES["iso"]), ("ISOSpeedRatings", "200"))

    def test_ignores_blank_values(self) -> None:
        self.assertIsNone(finder.lookup_raw({"Make": "   "}, finder.FIELD_ALIASES["make"]))
        self.assertIsNone(finder.lookup_raw({}, finder.FIELD_ALIASES["make"]))


class TextNormalizationTests(unittest.TestCase):
    def test_trims_collapses_and_casefolds(self) -> None:
        self.assertEqual(finder.normalize_text("  Canon   EOS  5D "), "canon eos 5d")

    def test_treats_underscores_like_spaces(self) -> None:
        self.assertEqual(finder.normalize_text("Canon_EOS_5D"), finder.normalize_text("Canon EOS 5D"))

    def test_returns_none_for_empty_values(self) -> None:
        self.assertIsNone(finder.normalize_text("   "))


class RationalNormalizationTests(unittest.TestCase):
    def test_parses_fractions_decimals_and_numbers(self) -> None:
        self.assertEqual(finder.normalize_rational("10/500"), finder.Fraction(1, 50))
        self.assertEqual(finder.normalize_rational("1/125"), finder.Fraction(1, 125))
        self.assertEqual(finder.normalize_rational("0.008"), finder.Fraction(1, 125))
        self.assertEqual(finder.normalize_rational(200), finder.Fraction(200))

    def test_ignores_units_around_the_number(self) -> None:
        self.assertEqual(finder.normalize_rational("50 mm"), finder.Fraction(50))
        self.assertEqual(finder.normalize_rational("f/5.6"), finder.Fraction(28, 5))

    def test_returns_none_for_unusable_values(self) -> None:
        self.assertIsNone(finder.normalize_rational("unknown"))
        self.assertIsNone(finder.normalize_rational("1/0"))
        self.assertIsNone(finder.normalize_rational(None))
        self.assertIsNone(finder.normalize_rational(True))


class ExposureNormalizationTests(unittest.TestCase):
    def test_equivalent_spellings_produce_one_key(self) -> None:
        keys = {
            finder.normalize_exposure("ExposureTime", "1/125"),
            finder.normalize_exposure("ExposureTime", "10/1250"),
            finder.normalize_exposure("ExposureTime", "0.008"),
            finder.normalize_exposure("ExposureTime", "1/125 sec"),
        }
        self.assertEqual(keys, {"1/125"})

    def test_absorbs_small_decimal_rounding(self) -> None:
        self.assertEqual(
            finder.normalize_exposure("ExposureTime", "0.0167"),
            finder.normalize_exposure("ExposureTime", "1/60"),
        )

    def test_converts_apex_shutter_speed_to_seconds(self) -> None:
        self.assertEqual(finder.normalize_exposure("ShutterSpeedValue", "6.965784"), "1/125")

    def test_long_exposures_are_expressed_in_seconds(self) -> None:
        self.assertEqual(finder.normalize_exposure("ExposureTime", "2.5"), "2.5s")

    def test_rejects_impossible_values(self) -> None:
        self.assertIsNone(finder.normalize_exposure("ExposureTime", "0"))
        self.assertIsNone(finder.normalize_exposure("ExposureTime", "n/a"))


class ApertureNormalizationTests(unittest.TestCase):
    def test_reads_fnumber_in_several_spellings(self) -> None:
        self.assertEqual(finder.normalize_aperture("FNumber", "56/10"), "5.6")
        self.assertEqual(finder.normalize_aperture("FNumber", "f/5.6"), "5.6")
        self.assertEqual(finder.normalize_aperture("FNumber", "5.60"), "5.6")

    def test_converts_apex_aperture_value(self) -> None:
        self.assertEqual(finder.normalize_aperture("ApertureValue", "4.970854"), "5.6")

    def test_rejects_unusable_values(self) -> None:
        self.assertIsNone(finder.normalize_aperture("FNumber", "unknown"))
        self.assertIsNone(finder.normalize_aperture("FNumber", "0"))


class SimpleFieldNormalizationTests(unittest.TestCase):
    def test_iso_is_reduced_to_an_integer(self) -> None:
        self.assertEqual(finder.normalize_iso("100"), "100")
        self.assertEqual(finder.normalize_iso("ISO 400"), "400")
        self.assertEqual(finder.normalize_iso(200), "200")

    def test_iso_uses_the_first_entry_of_a_joined_list(self) -> None:
        self.assertEqual(finder.normalize_iso("100; 0; 0"), "100")

    def test_focal_length_drops_units(self) -> None:
        self.assertEqual(finder.normalize_focal_length("50/1"), "50")
        self.assertEqual(finder.normalize_focal_length("50 mm"), "50")
        self.assertEqual(finder.normalize_focal_length("16.50"), "16.5")

    def test_orientation_and_dimensions_become_integers(self) -> None:
        self.assertEqual(finder.normalize_orientation("1"), "1")
        self.assertEqual(finder.normalize_dimension("4000"), "4000")
        self.assertIsNone(finder.normalize_dimension("0"))


class NormalizeMetadataTests(unittest.TestCase):
    def test_preserves_raw_values_and_records_the_source_tag(self) -> None:
        normalized = finder.normalize_metadata({"Make": "  CANON  ", "FNumber": "56/10"})
        self.assertEqual(normalized.fields["make"].raw, "CANON")
        self.assertEqual(normalized.fields["make"].source, "Make")
        self.assertEqual(normalized.comparison_key("make"), "canon")
        self.assertEqual(normalized.fields["aperture"].raw, "56/10")
        self.assertEqual(normalized.comparison_key("aperture"), "5.6")

    def test_camera_key_combines_make_and_model(self) -> None:
        first = finder.normalize_metadata({"Make": "Canon", "Model": "EOS 5D"})
        second = finder.normalize_metadata({"Make": "canon", "Model": "eos  5d"})
        self.assertEqual(first.comparison_key("camera"), second.comparison_key("camera"))

    def test_missing_and_malformed_fields_yield_none_without_raising(self) -> None:
        normalized = finder.normalize_metadata({"FNumber": "wide open", "Model": "EOS 5D"})
        self.assertIsNone(normalized.comparison_key("aperture"))
        self.assertIsNone(normalized.comparison_key("lens"))
        self.assertEqual(normalized.fields["aperture"].raw, "wide open")

    def test_reports_whether_any_usable_exif_was_found(self) -> None:
        self.assertFalse(finder.normalize_metadata({}).has_usable_exif)
        self.assertTrue(finder.normalize_metadata({"Make": "Canon"}).has_usable_exif)

    def test_raw_value_helper_formats_the_camera_for_display(self) -> None:
        normalized = finder.normalize_metadata({"Make": "Canon", "Model": "EOS 5D"})
        self.assertEqual(normalized.raw_value("camera"), "Canon EOS 5D")
        self.assertEqual(normalized.raw_value("lens"), "")


if __name__ == "__main__":
    unittest.main()
