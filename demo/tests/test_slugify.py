import unittest

from text_utils import slugify


class SlugifyAcceptance(unittest.TestCase):
    def test_words_and_punctuation(self):
        self.assertEqual(slugify("  Hello, World!  "), "hello-world")

    def test_mixed_separator_runs(self):
        self.assertEqual(slugify("A___B --- C\t\nD"), "a-b-c-d")

    def test_unicode_normalization(self):
        self.assertEqual(slugify("Crème brûlée & café"), "creme-brulee-cafe")

    def test_digits_and_already_valid_slug(self):
        self.assertEqual(slugify("version-42-release"), "version-42-release")

    def test_empty_or_no_ascii_letters_digits(self):
        for value in ("", " ___ ", "你好🌍"):
            with self.subTest(value=value):
                self.assertEqual(slugify(value), "")

    def test_rejects_non_string(self):
        for value in (None, 123, b"hello"):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    slugify(value)
