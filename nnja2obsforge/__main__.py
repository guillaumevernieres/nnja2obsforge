"""NNJA ocean/sea-ice observations -> obsForge-format files.

  python -m nnja2obsforge --start 20230101 --end 20230131 [--spaces 'insitu_*' rads_adt_j3]

For every selected obsForge space (mapping.yaml) and gdas cycle of the period: list the
NNJA files of the window, download those not cached (unless --no-fetch), and write
  <root>/obsforge/gdas.YYYYMMDD/HH/ocean/<dir>/gdas.tHHz.<space>.nc
Downloading needs internet (login or u1-service nodes); --no-fetch works anywhere.
"""

import argparse
import functools
import logging
import os
import sys

from . import bucket, convert, ioda, mapping

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASIN = ('/scratch3/NCEPDEV/da/Guillaume.Vernieres/runs/gfs-dev/mom6100_grid/'
         'RECCAP2_region_masks_all_v20221025.nc')
log = logging.getLogger('nnja2obsforge')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='nnja2obsforge', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--start', help='first cycle day, YYYYMMDD')
    ap.add_argument('--end', help='last cycle day, YYYYMMDD (default: --start)')
    ap.add_argument('--spaces', nargs='+', metavar='NAME',
                    help="obsForge names or globs, e.g. 'insitu_*' 'sst_*' rads_adt_j3 "
                         '(default: every enabled space; a disabled one must be named)')
    ap.add_argument('--cycles', nargs='+', type=int, metavar='HH',
                    help='override the cycle hours of every space (e.g. 0)')
    ap.add_argument('--root', default=ROOT,
                    help='holds cache/ and obsforge/ (default: the repository)')
    ap.add_argument('--out', help='output root (default: <root>/obsforge)')
    ap.add_argument('--cache', help='NNJA mirror (default: <root>/cache)')
    ap.add_argument('--mapping', default=mapping.DEFAULT)
    ap.add_argument('--basin-file', default=BASIN)
    ap.add_argument('--run', default='gdas', help='file prefix (default gdas)')
    ap.add_argument('--workers', type=int, default=16, help='parallel downloads')
    ap.add_argument('--no-fetch', action='store_true', help='use the cache only')
    ap.add_argument('--fetch-only', action='store_true', help='download, do not convert')
    ap.add_argument('--list', action='store_true',
                    help='print the mapping as a markdown table and exit')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

    spaces = mapping.select(mapping.load(args.mapping), args.spaces,
                            include_disabled=args.list and not args.spaces)
    if args.list:
        print(mapping.table(spaces))
        return 0
    if not args.start:
        ap.error('--start is required')
    if not spaces:
        ap.error(f'no space matches {args.spaces}')
    first = convert.parse_day(args.start)
    last = convert.parse_day(args.end or args.start)
    cache = args.cache or os.path.join(args.root, 'cache')
    out = args.out or os.path.join(args.root, 'obsforge')

    lister = (functools.partial(bucket.list_cache, cache) if args.no_fetch
              else bucket.list_s3)
    plan = {}
    for s in spaces:
        cyc = convert.cycles_of(s, first, last, args.cycles)
        beg, _ = convert.window(s, cyc[0])
        _, end = convert.window(s, cyc[-1])
        files = []
        for src in s.sources:
            files += [(f, src) for f in bucket.discover(src, beg, end, lister)]
        plan[s.name] = (s, cyc, files)
        log.info('%s: %d NNJA files for %d cycles', s.name, len(files), len(cyc))

    if not args.no_fetch:
        keys = sorted({f.key for _, _, files in plan.values() for f, _ in files})
        if bucket.fetch(keys, cache, args.workers)['error']:
            log.error('some downloads failed; rerun to retry them')
            return 1
    if args.fetch_only:
        return 0

    basins = ioda.Basins(args.basin_file)
    total = {}
    for name, (s, cyc, files) in plan.items():
        if s.sources and all(src['reader'] == 'obsforge' for src in s.sources):
            log.info('%s: converted by obsForge (python -m nnja2obsforge.obsforge_bufr)', name)
            continue
        counts = convert.run_space(s, cyc, files, cache, out, basins, args.run)
        for c, n in counts.items():
            total.setdefault(c, {})[name] = n
    for c in sorted(total):
        got = ', '.join(f'{k} {v}' for k, v in total[c].items() if v)
        log.info('%s.%s %sz: %s', args.run, f'{c:%Y%m%d}', f'{c:%H}', got or 'no obs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
