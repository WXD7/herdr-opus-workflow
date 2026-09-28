#!/usr/bin/env python3
"""The one definition of Herdr's LangWatch run label (herdr.run_id).

A new label is run-<hyphenated UUID>. The legacy run-<32 hex> label is one unbroken token that
LangWatch 3.17's CRYPTO rule can read as a bitcoin address and store as run-[CRYPTO]; the same
UUID written with its hyphens has no run long enough to match. So a legacy label maps to its own
UUID in canonical form, a canonical label maps to itself, and any other label is a custom
(business) label kept verbatim. Nothing is random except new_run_id().

Standard library only, Python 3.9+: shared by launch.py, the zsh entry points and collect_run.py.
CLI: run_id.py LABEL prints the normalized label and a newline. run_id.py --shell LABEL prints it
with no newline, for zsh to capture as "$(run_id.py --shell LABEL && printf .)" and then drop the
dot: a bare $(...) would also strip newlines that end a custom label.
"""
import os
import re
import sys
import uuid

LEGACY = re.compile(r'run-([0-9a-fA-F]{32})')
CANONICAL = re.compile(r'run-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def new_run_id():
    return 'run-' + str(uuid.uuid4())


def normalize_run_id(label):
    legacy = LEGACY.fullmatch(label)
    # The same 128-bit value re-spelled: distinct legacy labels stay distinct, repeats stay equal.
    return 'run-' + str(uuid.UUID(hex=legacy.group(1))) if legacy else label


def label_format(label):
    if CANONICAL.fullmatch(label):
        return 'canonical'
    return 'legacy_32_hex' if LEGACY.fullmatch(label) else 'custom'


if __name__ == '__main__':
    # Only a two-argument call is the shell mode, so any single argument (even --shell) is a label.
    shell = len(sys.argv) == 3 and sys.argv[1] == '--shell'
    if len(sys.argv) != 2 and not shell:
        sys.exit('Usage: run_id.py [--shell] LABEL')
    # Bytes out as received, so a custom label reaches the shell unchanged.
    sys.stdout.buffer.write(os.fsencode(normalize_run_id(sys.argv[-1])) + (b'' if shell else b'\n'))
