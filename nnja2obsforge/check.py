"""Check an nnja2obsforge database for completeness and garbage.

  python -m nnja2obsforge.check --start 20230101 --end 20230105 [--spaces ...]

A. Inventory: per space and cycle, the NNJA files the bucket has for the window (live S3
   listing; --offline uses the cache), whether they are all cached with the bucket's size,
   whether the output exists and its obs count. Status:
     OK, NO_SOURCE (bucket has nothing for the window: expected absence),
     MISSING (inputs exist, no output: FAIL), EMPTY (inputs, 0 obs kept: WARN),
     LOW (< 30% of the space's median count over the period: WARN),
     CACHE (input not cached / truncated / unreadable: FAIL).
B. Accounting: the inputs are re-read and the obs of the window counted, with the drops by
   reason (non-finite, PreQC, valid range, duplicates); kept must equal the file count.
C. Content of every output file (schema vs obsForge, fills, ranges, times, errors,
   duplicates, land fraction; in situ/SST/SSS vs WOA23 mean and std, downloaded into
   <cache>/woa23; ADT robust outliers); 6 h spaces: no record in two cycles.
D. Same-cycle comparison with obsForge (--ref, default common_obsForge) where it has the
   same platform: counts, collocated differences (scipy), sea-ice box means.
E. Report in <root>/reports/<start>-<end>/: report.md, *.csv, figures (matplotlib).
Exit status 1 if any FAIL.
"""

import argparse
import csv
import functools
import logging
import os
import sys
from collections import defaultdict

import numpy as np

from . import bucket, convert, ioda, mapping, qa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = '/scratch3/NCEPDEV/da/common_obsForge'
log = logging.getLogger('nnja2obsforge.check')


class S3Sizes:
    """S3 lister that remembers object sizes."""

    def __init__(self):
        self.size = {}

    def __call__(self, prefix):
        import urllib.parse
        import urllib.request
        import xml.etree.ElementTree as ET
        ns = '{http://s3.amazonaws.com/doc/2006-03-01/}'
        keys, token = [], None
        while True:
            q = {'list-type': '2', 'prefix': prefix.rstrip('/') + '/'}
            if token:
                q['continuation-token'] = token
            with urllib.request.urlopen(f'{bucket.URL}/?{urllib.parse.urlencode(q)}',
                                        timeout=60) as r:
                root = ET.fromstring(r.read())
            for c in root.findall(ns + 'Contents'):
                k = c.findtext(ns + 'Key')
                keys.append(k)
                self.size[k] = int(c.findtext(ns + 'Size'))
            token = root.findtext(ns + 'NextContinuationToken')
            if not token:
                return keys


def cache_state(files, cache, sizes):
    """FAIL messages for inputs that are not usable in the cache."""
    bad = []
    for f, _ in files:
        p = os.path.join(cache, f.key)
        if not os.path.exists(p):
            bad.append(f'not cached: {f.key}')
        elif f.key in sizes and os.path.getsize(p) != sizes[f.key]:
            bad.append(f'size {os.path.getsize(p)} != bucket {sizes[f.key]}: {f.key}')
    return bad


def account(c, space, beg, end):
    """Counts of the obs of [beg, end) through the converter's filters, independently."""
    kind = space.kind
    b = (beg - ioda.EPOCH).total_seconds()
    e = (end - ioda.EPOCH).total_seconds()
    t = c['t']
    inw = np.isfinite(t) & (t >= b) & ((t <= e) if space.window['closed'] else (t < e))
    a = {'in_window': int(inw.sum())}
    fin = np.isfinite(c['lat']) & np.isfinite(c['lon']) & np.isfinite(c['y'])
    if 'depth' in c:
        fin &= np.isfinite(c['depth']) & (c['depth'] >= 0)
    a['non_finite'] = int((inw & ~fin).sum())
    keep = inw & fin
    prof = convert.profile_reject(c, keep, kind)
    a['profile'] = int((keep & prof).sum())
    keep &= ~prof
    if kind['preqc'] is not None:
        q = np.isin(c['qc'], kind['preqc'])
        a['preqc'] = int((keep & ~q).sum())
        keep &= q
    else:
        a['preqc'] = 0
    lo, hi = kind['valid']
    v = (c['y'] >= lo) & (c['y'] <= hi)
    a['valid'] = int((keep & ~v).sum())
    keep &= v
    keys = [np.round(c['t'][keep]), np.round(c['lat'][keep] * 1e5),
            np.round(((c['lon'][keep] + 180.0) % 360.0 - 180.0) * 1e5)]
    if 'depth' in c:
        keys.append(np.round(c['depth'][keep] * 100))
    n = int(keep.sum())
    nu = np.unique(np.stack(keys, 1).astype(np.int64), axis=0).shape[0] if n else 0
    a['duplicate'] = n - nu
    a['kept'] = nu
    return a


def main(argv=None):
    ap = argparse.ArgumentParser(prog='nnja2obsforge.check', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--start', required=True)
    ap.add_argument('--end')
    ap.add_argument('--spaces', nargs='+')
    ap.add_argument('--cycles', nargs='+', type=int)
    ap.add_argument('--root', default=ROOT)
    ap.add_argument('--out', help='database to check (default <root>/obsforge)')
    ap.add_argument('--cache')
    ap.add_argument('--mapping', default=mapping.DEFAULT)
    ap.add_argument('--ref', default=REF, help='obsForge COMROOT for the comparison '
                    "('' to skip)")
    ap.add_argument('--report', help='report directory (default <root>/reports/<start>-<end>)')
    ap.add_argument('--run', default='gdas')
    ap.add_argument('--offline', action='store_true',
                    help='expected inputs from the cache instead of the S3 listing')
    ap.add_argument('--no-plots', action='store_true')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    first = convert.parse_day(args.start)
    last = convert.parse_day(args.end or args.start)
    cache = args.cache or os.path.join(args.root, 'cache')
    out = args.out or os.path.join(args.root, 'obsforge')
    rep = args.report or os.path.join(args.root, 'reports',
                                      f'{first:%Y%m%d}-{last:%Y%m%d}')
    os.makedirs(rep, exist_ok=True)
    spaces = mapping.select(mapping.load(args.mapping), args.spaces)
    sizes = S3Sizes()
    lister = functools.partial(bucket.list_cache, cache) if args.offline else sizes
    woa = qa.Woa(os.path.join(cache, 'woa23'), offline=args.offline)

    inv, acc, cont, refrows, issues = [], [], [], [], []
    counts = defaultdict(dict)
    diffs = {}

    def flag(level, space, cycle, msg):
        issues.append((level, space, f'{cycle:%Y%m%d%H}' if cycle else '', msg))

    for s in spaces:
        cyc = convert.cycles_of(s, first, last, args.cycles)
        beg, _ = convert.window(s, cyc[0])
        _, end = convert.window(s, cyc[-1])
        files = []
        for src in s.sources:
            files += [(f, src) for f in bucket.discover(src, beg, end, lister)]
        reader = convert.Reader(cache, keep=48)
        prev = None
        log.info('%s: %d bucket files', s.name, len(files))
        for cycle in cyc:
            wb, we = convert.window(s, cycle)
            mine = [(f, src) for f, src in files if f.overlaps(wb, we, s.window['closed'])]
            path = convert.out_path(out, s, cycle, args.run)
            row = {'space': s.name, 'cycle': f'{cycle:%Y%m%d%H}', 'src_files': len(mine),
                   'output': os.path.exists(path), 'n_obs': 0, 'status': ''}
            bad = cache_state(mine, cache, sizes.size)
            for b in bad:
                flag('FAIL', s.name, cycle, b)
            n_file = None
            if row['output']:
                try:
                    qa.load(path)
                except (OSError, KeyError, IndexError, ValueError) as e:
                    m, iss, cols = {'n': 0}, [('FAIL', f'unreadable output: {e}')], None
                else:
                    m, iss, cols = qa.content(path, s, cycle, woa)
                n_file = m['n']
                row['n_obs'] = n_file
                cont.append(dict(space=s.name, cycle=row['cycle'],
                                 **{k: v for k, v in m.items()}))
                for lev, msg in iss:
                    flag(lev, s.name, cycle, msg)
                ref = qa.schema_ref(s.name)
                if ref:
                    for lev, msg in qa.compare_schema(path, ref):
                        flag(lev, s.name, cycle, msg)
                # 6 h spaces: a record must not be in two consecutive cycles
                if cols is not None and s.window['half'] <= 3:
                    k = qa.record_keys(cols)
                    if prev is not None:
                        both = np.intersect1d(prev, k).size
                        if both:
                            flag('FAIL', s.name, cycle, f'{both} records also in the '
                                                         'previous cycle')
                    prev = k
                # D. obsForge comparison
                if (args.ref and cols is not None and s.name in qa.PAIRS):
                    rp = qa.ref_path(args.ref, s.name, cycle, args.run)
                    if rp:
                        rc = qa.load(rp)
                        if s.kind_name == 'icec':
                            r = qa.boxes(cols, rc, cycle)
                        else:
                            try:
                                r = qa.collocate(cols, rc, s.kind_name, cycle)
                            except ImportError:
                                r = {'note': 'scipy missing'}
                        d = r.pop('_diff', None)
                        if d is not None:
                            diffs.setdefault(s.name, []).append(d)
                        if 'diff_median' in r and abs(r['diff_median']) > r['diff_warn']:
                            flag('WARN', s.name, cycle, f"median NNJA-obsForge difference "
                                 f"{r['diff_median']:.3f}")
                        refrows.append(dict(space=s.name, cycle=row['cycle'],
                                            ref=os.path.relpath(rp, args.ref), **r))
            # B. accounting
            if mine and not bad:
                c, used = convert.gather([(f, src, s.kind) for f, src in mine], reader)
                if c is not None:
                    a = account(c, s, wb, we)
                    acc.append(dict(space=s.name, cycle=row['cycle'], files_read=len(used),
                                    files_listed=len(mine), file_count=n_file, **a))
                    if len(used) < len(mine):
                        flag('INFO', s.name, cycle, f'{len(mine) - len(used)} of {len(mine)} '
                                                    'inputs hold no valid value')
                    if (n_file or 0) != a['kept']:
                        flag('FAIL', s.name, cycle, f"file has {n_file or 0} obs, inputs give "
                                                    f"{a['kept']}")
                    if a['in_window'] and a['valid'] > 0.2 * a['in_window']:
                        flag('WARN', s.name, cycle, f"{a['valid']} of {a['in_window']} obs "
                                                    'outside the valid range')
                    if a['in_window'] and a['preqc'] > 0.5 * a['in_window']:
                        flag('WARN', s.name, cycle, f"{a['preqc']} of {a['in_window']} obs "
                                                    'rejected by PreQC')
                elif mine:
                    flag('WARN', s.name, cycle, 'inputs listed but none has data')
            # A. status
            if bad:
                row['status'] = 'CACHE'
            elif not mine:
                row['status'] = 'UNEXPECTED' if row['output'] else 'NO_SOURCE'
            elif not row['output']:
                kept = acc[-1]['kept'] if acc and acc[-1]['cycle'] == row['cycle'] \
                    and acc[-1]['space'] == s.name else None
                row['status'] = 'EMPTY' if kept == 0 else 'MISSING'
            else:
                row['status'] = 'OK'
            if row['status'] == 'MISSING':
                flag('FAIL', s.name, cycle, 'inputs exist but no output file')
            elif row['status'] == 'EMPTY':
                flag('WARN', s.name, cycle, 'inputs exist but no obs passes the filters')
            elif row['status'] == 'UNEXPECTED':
                flag('WARN', s.name, cycle, 'output exists without bucket inputs')
            counts[s.name][row['cycle']] = row['n_obs']
            inv.append(row)
        # LOW: count far below the space's median
        ok = [r for r in inv if r['space'] == s.name and r['status'] == 'OK']
        if len(ok) >= 3:
            med = np.median([r['n_obs'] for r in ok])
            for r in ok if med >= 50 else []:
                if r['n_obs'] < 0.3 * med:
                    r['status'] = 'LOW'
                    issues.append(('WARN', s.name, r['cycle'],
                                   f"{r['n_obs']} obs, < 30% of the median {med:.0f}"))

    def write_csv(name, rows):
        if not rows:
            return
        cols = []
        for r in rows:
            cols += [k for k in r if k not in cols]
        with open(os.path.join(rep, name), 'w', newline='') as f:
            w = csv.DictWriter(f, cols)
            w.writeheader()
            w.writerows(rows)

    write_csv('inventory.csv', inv)
    write_csv('accounting.csv', acc)
    write_csv('content.csv', cont)
    write_csv('reference.csv', refrows)
    write_csv('issues.csv', [dict(level=a, space=b, cycle=c, message=d)
                             for a, b, c, d in issues])
    figs = [] if args.no_plots else plots(rep, spaces, inv, counts, diffs, out, args.run)
    report(rep, args, first, last, spaces, inv, acc, refrows, issues, figs)
    nfail = sum(1 for i in issues if i[0] == 'FAIL')
    nwarn = sum(1 for i in issues if i[0] == 'WARN')
    log.info('report: %s (%d FAIL, %d WARN)', os.path.join(rep, 'report.md'), nfail, nwarn)
    return 1 if nfail else 0


def report(rep, args, first, last, spaces, inv, acc, refrows, issues, figs):
    by = defaultdict(lambda: defaultdict(int))
    tot = defaultdict(int)
    for r in inv:
        by[r['space']][r['status']] += 1
        tot[r['space']] += r['n_obs']
    statuses = ['OK', 'LOW', 'EMPTY', 'NO_SOURCE', 'MISSING', 'CACHE', 'UNEXPECTED']
    nfail = defaultdict(int)
    nwarn = defaultdict(int)
    for lev, sp, _, _ in issues:
        if lev == 'FAIL':
            nfail[sp] += 1
        elif lev == 'WARN':
            nwarn[sp] += 1
    L = [f'# nnja2obsforge check {first:%Y-%m-%d} .. {last:%Y-%m-%d}', '',
         f'Database `{args.out or os.path.join(args.root, "obsforge")}`, '
         f'{"cache listing" if args.offline else "live S3 listing"}, '
         f'reference `{args.ref or "none"}`.', '',
         f'**{sum(nfail.values())} FAIL, {sum(nwarn.values())} WARN** '
         f'({len(spaces)} spaces, {len(inv)} space-cycles).', '']
    have = [s.name for s in spaces if by[s.name]['NO_SOURCE'] < sum(by[s.name].values())]
    none = [s.name for s in spaces if s.name not in have]
    L += [f'NNJA has data for **{len(have)}** of the {len(spaces)} spaces in this period; '
          f'none for: {", ".join(none) or "-"}.', '']
    L += ['## Inventory', '', '| space | ' + ' | '.join(statuses) + ' | obs | FAIL | WARN |',
          '|---' * (len(statuses) + 4) + '|']
    for s in spaces:
        if s.name in none:
            continue
        b = by[s.name]
        L.append(f'| `{s.name}` | ' + ' | '.join(str(b[k] or '') for k in statuses)
                 + f' | {tot[s.name]} | {nfail[s.name] or ""} | {nwarn[s.name] or ""} |')
    L += ['']
    if acc:
        L += ['## Accounting (obs of the windows, summed over cycles)', '',
              '| space | in window | bad position/depth | bad profile | PreQC | valid range | '
              'duplicate | kept | file |', '|---|---|---|---|---|---|---|---|---|']
        agg = defaultdict(lambda: defaultdict(int))
        for a in acc:
            for k in ('in_window', 'non_finite', 'profile', 'preqc', 'valid', 'duplicate',
                      'kept'):
                agg[a['space']][k] += a[k]
            agg[a['space']]['file'] += a['file_count'] or 0
        for sp, a in agg.items():
            L.append(f"| `{sp}` | {a['in_window']} | {a['non_finite']} | {a['profile']} | "
                     f"{a['preqc']} | "
                     f"{a['valid']} | {a['duplicate']} | {a['kept']} | {a['file']} |")
        L += ['']
    if refrows:
        L += ['## Comparison with obsForge (same cycles)', '',
              '| space | cycles | NNJA obs | obsForge obs | matched | median diff | '
              'robust std | box \\|diff\\| | box corr |', '|---' * 9 + '|']
        agg = defaultdict(list)
        for r in refrows:
            agg[r['space']].append(r)
        for sp, rs in agg.items():
            def tot_(k):
                return sum(r.get(k, 0) or 0 for r in rs)

            def mean_(k):
                v = [r[k] for r in rs if r.get(k) is not None]
                return f'{np.mean(v):.3g}' if v else ''
            na = tot_('n_nnja_6h') or tot_('n_nnja')
            nb = tot_('n_ref_6h') or tot_('n_ref')
            L.append(f"| `{sp}` | {len(rs)} | {na} | {nb} | {mean_('matched_frac')} | "
                     f"{mean_('diff_median')} | {mean_('diff_rstd')} | "
                     f"{mean_('box_mean_absdiff')} | {mean_('box_corr')} |")
        L += ['', 'In situ and ADT: NNJA obs with true time in [T-3h, T+3h], matched within '
              '5 km/1 h/2 m (in situ) or 7 km/60 s (ADT; obsForge = RADS `adt_old`). '
              'Sea ice: 1° box means vs obsForge AMSR2 (different sensor).', '']
    L += ['## Issues', '']
    info = [i for i in issues if i[0] == 'INFO']
    issues = [i for i in issues if i[0] != 'INFO']
    if issues:
        grouped = defaultdict(list)
        for lev, sp, cy, msg in issues:
            grouped[(lev, sp, msg if 'not cached' not in msg else 'input not cached')].append(cy)
        L += ['| level | space | message | cycles |', '|---|---|---|---|']
        for (lev, sp, msg), cys in sorted(grouped.items()):
            cy = ', '.join(sorted(set(c for c in cys if c)))
            L.append(f'| {lev} | `{sp}` | {msg} | {cy if len(cys) < 6 else f"{len(cys)} cycles"} |')
    else:
        L.append('None.')
    if info:
        L += ['', f'{len(info)} INFO notes (e.g. mooring files with no valid value) in '
              'issues.csv.']
    L += ['']
    for f in figs:
        L += [f'![{f}]({f})', '']
    L += ['Files: inventory.csv, accounting.csv, content.csv, reference.csv, issues.csv.']
    with open(os.path.join(rep, 'report.md'), 'w') as f:
        f.write('\n'.join(L) + '\n')


def plots(rep, spaces, inv, counts, diffs, out, run):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.colors import LogNorm
    except ImportError:
        log.warning('matplotlib missing: no figures')
        return []
    figs = []
    names = [s.name for s in spaces if any(counts[s.name].values())]
    cycles = sorted({r['cycle'] for r in inv})
    if names:
        a = np.full((len(names), len(cycles)), np.nan)
        for i, n in enumerate(names):
            for j, c in enumerate(cycles):
                v = counts[n].get(c)
                if v:
                    a[i, j] = v
        fig, ax = plt.subplots(figsize=(1.0 + 0.35 * len(cycles), 1.0 + 0.28 * len(names)))
        im = ax.imshow(a, aspect='auto', norm=LogNorm(), cmap='viridis')
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=7)
        ax.set_xticks(range(len(cycles)))
        ax.set_xticklabels([c[4:] for c in cycles], rotation=90, fontsize=7)
        st = {(r['space'], r['cycle']): r['status'] for r in inv}
        for i, n in enumerate(names):
            for j, c in enumerate(cycles):
                s = st.get((n, c), '')
                if s and s not in ('OK', 'NO_SOURCE'):
                    ax.text(j, i, s[0], ha='center', va='center', color='r', fontsize=7)
        fig.colorbar(im, ax=ax, label='obs per file')
        ax.set_title('obs count per space and cycle (blank: no file; red letter: status)')
        fig.tight_layout()
        fig.savefig(os.path.join(rep, 'counts.png'), dpi=120)
        plt.close(fig)
        figs.append('counts.png')
        # map of the middle cycle with data, one panel per space
        mid = cycles[len(cycles) // 2]
        sp = {s.name: s for s in spaces}
        avail = [n for n in names if counts[n].get(mid)]
        if avail:
            ncol = 4
            nrow = (len(avail) + ncol - 1) // ncol
            fig, axs = plt.subplots(nrow, ncol, figsize=(16, 2.6 * nrow), squeeze=False)
            from datetime import datetime
            cyc = datetime.strptime(mid, '%Y%m%d%H')
            for ax, n in zip(axs.flat, avail):
                c = qa.load(convert.out_path(out, sp[n], cyc, run))
                o = np.argsort(c.get('depth', np.zeros_like(c['y'])))[::-1]
                sc = ax.scatter(c['lon'][o], c['lat'][o], c=c['y'][o], s=1, linewidths=0,
                                cmap='turbo', rasterized=True)
                ax.set_xlim(-180, 180)
                ax.set_ylim(-90, 90)
                ax.set_title(f"{n} (n={c['y'].size})", fontsize=8)
                ax.tick_params(labelsize=6)
                fig.colorbar(sc, ax=ax, shrink=0.8)
            for ax in list(axs.flat)[len(avail):]:
                ax.axis('off')
            fig.suptitle(f'values, cycle {mid} (profiles: shallowest on top)')
            fig.tight_layout()
            fig.savefig(os.path.join(rep, 'maps.png'), dpi=110)
            plt.close(fig)
            figs.append('maps.png')
    if diffs:
        n = len(diffs)
        fig, axs = plt.subplots(1, n, figsize=(3.2 * n, 2.8), squeeze=False)
        for ax, (k, d) in zip(axs.flat, diffs.items()):
            d = np.concatenate(d)
            lim = np.percentile(np.abs(d), 99) or 1e-3
            ax.hist(np.clip(d, -lim, lim), bins=60)
            ax.set_yscale('log')
            ax.set_title(k, fontsize=8)
            ax.tick_params(labelsize=6)
        fig.suptitle('NNJA - obsForge, collocated (sea ice: 1° box means)')
        fig.tight_layout()
        fig.savefig(os.path.join(rep, 'diffs.png'), dpi=110)
        plt.close(fig)
        figs.append('diffs.png')
    return figs


if __name__ == '__main__':
    sys.exit(main())
