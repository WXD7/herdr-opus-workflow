# Two-lane dispatch smoke test

Small Python 3 project using only the standard library. Two independent TODOs
let a supervisor dispatch separate workers and verify their results.

## Lane 1: text utility

Implement `text_utils.slugify(text)` in `text_utils.py`.

- Accept a string, otherwise raise `TypeError`.
- Apply Unicode NFKD normalization, then drop non-ASCII characters.
- Lowercase letters and replace each run of characters outside `a-z0-9`
  with one hyphen. Strip leading and trailing hyphens.
- Empty input or input with no remaining ASCII letters/digits returns `""`.

Verification: `python3 -m unittest tests.test_slugify -v`

## Lane 2: number utility

Implement `number_utils.summarize_numbers(values)` in `number_utils.py`.

- Accept an iterable of finite `int` or `float` values, including generators.
- Reject booleans and other element types with `TypeError`; reject NaN or
  infinity with `ValueError`.
- Return a dictionary containing `count`, `total`, `minimum`, `maximum`,
  and `mean`.
- Empty input returns count/total zero and minimum/maximum/mean `None`.
- Do not mutate the input.

Verification: `python3 -m unittest tests.test_numbers -v`

## Combined acceptance

Run `python3 -m unittest discover -s tests -v` from this directory.
The initial baseline intentionally raises `NotImplementedError` in both
functions. Tests are acceptance specifications prepared before worker code.
Workers should only change their own implementation file and must not alter
the tests or the other lane's file.
