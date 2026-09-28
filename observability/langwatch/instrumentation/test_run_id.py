import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('herdr_run_id', ROOT / 'run_id.py')
run_ids = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_ids)

# Supervisor session of run tm0ivs, whose legacy label LangWatch 3.17 stored as run-[CRYPTO].
SESSION = '18b2d22b-75ce-4444-8e9b-89e8735e951f'
REDACTED = 'run-18b2d22b75ce44448e9b89e8735e951f'
# The two CRYPTO patterns of LangWatch essentialPii.ts (../source/platform/app/src/server/
# data-privacy/redaction). Upstream main now also checksums a bitcoin match; the pinned 3.17
# container evidently did not, since it stored run-[CRYPTO] for REDACTED.
CRYPTO_SHAPES = (re.compile(r'\b0x[a-fA-F0-9]{40}\b', re.ASCII),
                 re.compile(r'\b(?:bc1[a-z0-9]{25,62}|BC1[A-Z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b',
                            re.ASCII))


def crypto_shaped(label):
    return any(pattern.search(label) for pattern in CRYPTO_SHAPES)


class RunIdTest(unittest.TestCase):
    def test_redacted_label_becomes_its_own_session_uuid_outside_the_crypto_shape(self):
        self.assertTrue(crypto_shaped(REDACTED))
        self.assertEqual(run_ids.normalize_run_id(REDACTED), 'run-' + SESSION)
        self.assertFalse(crypto_shaped('run-' + SESSION))
        # Same value in capitals is the same UUID, as the resume check already treats it.
        self.assertEqual(run_ids.normalize_run_id('run-' + REDACTED[4:].upper()), 'run-' + SESSION)

    def test_canonical_labels_never_fit_a_crypto_shape(self):
        # No hyphen-free run is longer than 12 characters; the shapes need 26 or more.
        labels = [run_ids.new_run_id() for _ in range(2000)] + ['run-11111111-1111-4111-8111-111111111111',
                                                               'run-33333333-3333-4333-b333-333333333333']
        self.assertEqual([x for x in labels if crypto_shaped(x)], [])

    def test_distinct_legacy_labels_never_collide(self):
        other = REDACTED[:-1] + 'e'
        self.assertEqual(run_ids.normalize_run_id(other), 'run-18b2d22b-75ce-4444-8e9b-89e8735e951e')
        self.assertNotEqual(run_ids.normalize_run_id(other), run_ids.normalize_run_id(REDACTED))
        sessions = [uuid.uuid4() for _ in range(2000)]
        mapped = [run_ids.normalize_run_id('run-' + session.hex) for session in sessions]
        self.assertEqual(mapped, ['run-' + str(session) for session in sessions])
        self.assertEqual(len(set(mapped)), len(set(sessions)))

    def test_normalizing_is_idempotent_and_repeatable(self):
        for label in (REDACTED, 'run-' + SESSION, 'tm0ivs', 'run-' + SESSION.upper(), run_ids.new_run_id()):
            with self.subTest(label=label):
                once = run_ids.normalize_run_id(label)
                self.assertEqual(run_ids.normalize_run_id(once), once)
                self.assertEqual(run_ids.normalize_run_id(label), once)

    def test_custom_and_near_miss_labels_are_kept_verbatim(self):
        hexes = REDACTED[4:]
        for label in ('tm0ivs', 'herdr-demo-20260926', 'run-fixture', 'telemetry-fixture', 'synthetic-gate-demo',
                      'run-repair-20260927-id-format', '开发 run 标签', '', hexes, 'RUN-' + hexes, 'run_' + hexes,
                      'run-' + hexes[:-1], 'run-' + hexes + 'a', 'run-' + hexes + '-2', 'run-' + hexes[:-1] + 'g',
                      ' ' + REDACTED, REDACTED + ' ', REDACTED + '\n', 'run-18b2d22b75ce-4444-8e9b-89e8735e951f',
                      'run-' + SESSION.upper(), 'run-{' + SESSION + '}', 'run-urn:uuid:' + SESSION,
                      'run-' + '١' * 32, 'run-' + 'ａ' * 32):
            with self.subTest(label=label):
                self.assertEqual(run_ids.normalize_run_id(label), label)

    def test_label_format_names_the_three_cases(self):
        self.assertEqual(run_ids.label_format(REDACTED), 'legacy_32_hex')
        self.assertEqual(run_ids.label_format('run-' + SESSION), 'canonical')
        for label in ('tm0ivs', 'run-' + SESSION.upper(), REDACTED + '\n'):
            self.assertEqual(run_ids.label_format(label), 'custom')

    def test_new_labels_are_distinct_canonical_uuid4(self):
        labels = {run_ids.new_run_id() for _ in range(500)}
        self.assertEqual(len(labels), 500)
        for label in labels:
            self.assertEqual(run_ids.label_format(label), 'canonical')
            self.assertEqual(uuid.UUID(label[4:]).version, 4)

    def test_cli_prints_the_label_byte_for_byte(self):
        cli = [sys.executable, str(ROOT / 'run_id.py')]
        for label, expected in ((REDACTED, 'run-' + SESSION), ('run-' + SESSION, 'run-' + SESSION),
                                ('-n', '-n'), ('--shell', '--shell'), ('herdr demo 开发', 'herdr demo 开发'),
                                ('tm0ivs\n', 'tm0ivs\n'), ('tm0ivs\n\n\n', 'tm0ivs\n\n\n'), ('\n', '\n'),
                                (REDACTED + '\n', REDACTED + '\n')):
            with self.subTest(label=label):
                out = subprocess.run(cli + [label], capture_output=True, check=True)
                self.assertEqual(out.stdout, expected.encode() + b'\n')
                # The zsh entries' mode: the same bytes with no line ending for $(...) to strip.
                out = subprocess.run(cli + ['--shell', label], capture_output=True, check=True)
                self.assertEqual(out.stdout, expected.encode())
        for argv in ([], ['a', 'b'], ['--shell', 'a', 'b']):
            out = subprocess.run(cli + argv, capture_output=True)
            self.assertNotEqual(out.returncode, 0)
            self.assertEqual(out.stdout, b'')


if __name__ == '__main__':
    unittest.main()
