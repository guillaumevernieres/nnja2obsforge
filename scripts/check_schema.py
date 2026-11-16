#!/usr/bin/env python3
"""Schema and content checks of nnja2obsforge files, without inventory or comparisons
(the full check is `python -m nnja2obsforge.check`).

usage: scripts/check_schema.py DIR [DIR ...]     (any directory holding gdas.YYYYMMDD/HH)
"""

import glob
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nnja2obsforge import mapping, qa  # noqa: E402


def main(dirs):
    spaces = {s.name: s for s in mapping.load()}
    files = sorted({f for d in dirs for f in glob.glob(f'{d}/**/*.t??z.*.nc', recursive=True)})
    nbad = 0
    for f in files:
        m = re.search(r'\.(\d{8})/(\d{2})/ocean/[^/]+/\w+\.t\d\dz\.(\w+)\.nc$', f)
        if not m or m.group(3) not in spaces:
            continue
        cycle = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H')
        s = spaces[m.group(3)]
        metrics, issues, _ = qa.content(f, s, cycle)
        ref = qa.schema_ref(s.name)
        if ref:
            issues += qa.compare_schema(f, ref)
        bad = any(lev == 'FAIL' for lev, _ in issues)
        nbad += bad
        print(f"{'BAD' if bad else 'OK '} {cycle:%Y%m%d%H} {s.name}: n={metrics['n']}")
        for lev, msg in issues:
            print(f'      {lev} {msg}')
    print(f'{len(files)} files, {nbad} with problems')
    return int(nbad > 0)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
