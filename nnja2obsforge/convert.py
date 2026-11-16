"""Cycle windows, selection, re-dating and output of one obsForge space."""

import logging
import os
from collections import OrderedDict
from datetime import datetime, timedelta

import numpy as np

from . import bucket, ioda

log = logging.getLogger(__name__)
COLS = ('t', 'lat', 'lon', 'y', 'err', 'qc', 'depth', 'station')


def window(space, cycle):
    """[beg, end) of the obs kept for a cycle."""
    h = timedelta(hours=space.window['half'])
    return cycle - h, cycle + h


def out_path(root, space, cycle, run='gdas'):
    return os.path.join(root, f'{run}.{cycle:%Y%m%d}', f'{cycle:%H}', 'ocean',
                        space.kind['dir'], f'{run}.t{cycle:%H}z.{space.name}.nc')


def cycles_of(space, first_day, last_day, hours=None):
    hours = space.window['cycles'] if hours is None else hours
    out, d = [], first_day
    while d <= last_day:
        out += [d + timedelta(hours=int(h)) for h in sorted(hours)]
        d += timedelta(days=1)
    return out


class Reader:
    """Reads cached files, keeping the last few in memory (daily files serve 4+ cycles)."""

    def __init__(self, cache, keep=12):
        self.cache, self.keep, self.mem = cache, keep, OrderedDict()

    def __call__(self, f, source, kind):
        k = (f.key, kind['var'])
        if k in self.mem:
            self.mem.move_to_end(k)
            return self.mem[k]
        path = os.path.join(self.cache, f.key)
        c = None
        if os.path.exists(path):
            try:
                c = ioda.READERS[source['reader']](path, kind['aliases'], kind['err'])
            except (OSError, KeyError, IndexError, ValueError) as e:
                log.warning('%s: unreadable (%s)', f.key, e)
        self.mem[k] = c
        if len(self.mem) > self.keep:
            self.mem.popitem(last=False)
        return c


def gather(files, read):
    """Concatenate the columns of (file, source) pairs; None when there is nothing."""
    parts, used = [], []
    for f, src, kind in files:
        c = read(f, src, kind)
        if c is not None and c['t'].size:
            parts.append(c)
            used.append(os.path.basename(f.key))
    if not parts:
        return None, used
    n = [p['t'].size for p in parts]
    out = {}
    for k in COLS:
        if any(k in p for p in parts):
            fill = np.nan if k != 'station' else ioda.FILL['i4']
            out[k] = np.concatenate([p[k] if k in p else np.full(m, fill)
                                     for p, m in zip(parts, n)])
    return out, used


def profile_reject(c, ok, kind):
    """Rows of profiles (same time and position) in which at least kind['profile_reject']
    of the levels are rejected by the NNJA PreQC: such profiles are mislocated or bad as a
    whole (NNJA's WOA check flags only the levels > 10 degC / 2 psu off)."""
    frac = kind.get('profile_reject')
    if frac is None or kind['preqc'] is None or 'depth' not in c:
        return np.zeros(c['t'].size, bool)
    idx = np.flatnonzero(ok)
    key = np.stack([np.round(c['t'][idx]), np.round(c['lat'][idx] * 1e4),
                    np.round(c['lon'][idx] * 1e4)], 1).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    rej = ~np.isin(c['qc'][idx], kind['preqc'])
    share = np.bincount(inv, weights=rej) / np.bincount(inv)
    out = np.zeros(c['t'].size, bool)
    out[idx] = share[inv] >= frac
    return out


def select(c, space, beg, end):
    """Valid obs of [beg, end) ([beg, end] if window.closed), duplicates removed,
    sorted by (time, lat, lon[, depth])."""
    kind = space.kind
    b = (beg - ioda.EPOCH).total_seconds()
    e = (end - ioda.EPOCH).total_seconds()
    vmin, vmax = kind['valid']
    ok = (np.isfinite(c['t']) & np.isfinite(c['lat']) & np.isfinite(c['lon'])
          & np.isfinite(c['y'])
          & (c['t'] >= b) & ((c['t'] <= e) if space.window['closed'] else (c['t'] < e)))
    if 'depth' in c:
        ok &= np.isfinite(c['depth']) & (c['depth'] >= 0)
    ok &= ~profile_reject(c, ok, kind)
    ok &= (c['y'] >= vmin) & (c['y'] <= vmax)
    if kind['preqc'] is not None:
        ok &= np.isin(c['qc'], kind['preqc'])
    c = {k: v[ok] for k, v in c.items()}
    c['t'] = np.round(c['t']).astype(np.int64)
    c['lon'] = (c['lon'] + 180.0) % 360.0 - 180.0
    keys = [c['t'], np.round(c['lat'] * 1e5).astype(np.int64),
            np.round(c['lon'] * 1e5).astype(np.int64)]
    if 'depth' in c:
        keys.append(np.round(c['depth'] * 100).astype(np.int64))
    if c['t'].size == 0:
        return c
    _, first = np.unique(np.stack(keys, axis=1), axis=0, return_index=True)
    return {k: v[first] for k, v in c.items()}


def redate(c, space, cycle):
    """Move obs outside [T-3h, T+3h] to its edges (obsForge Rads2Ioda::reDate):
    ObsError += redate_error_ratio [unit/day] * time moved; t0 keeps the true time."""
    ct = int((cycle - ioda.EPOCH).total_seconds())
    wb, we = ct - 3 * 3600, ct + 3 * 3600
    t = c['t']
    early, late = t < wb, t > we
    c['t0'] = t.copy()
    ratio = space.window['redate_error_ratio']
    c['err'] = c['err'] + ratio / 86400.0 * (np.where(early, wb - t, 0)
                                             + np.where(late, t - we, 0))
    c['t'] = np.where(early, wb + 1, np.where(late, we - 1, t))
    return int(early.sum()), int(late.sum())


def run_space(space, cycles, files, cache, root, basins, run='gdas'):
    """Write the files of one space for the cycles. files: discovered (File, source) pairs.
    Returns {cycle: obs count}."""
    read = Reader(cache)
    counts = {}
    for cycle in cycles:
        beg, end = window(space, cycle)
        mine = [(f, s, space.kind) for f, s in files
                if f.overlaps(beg, end, space.window['closed'])]
        c, used = gather(mine, read)
        if c is None:
            counts[cycle] = 0
            continue
        c = select(c, space, beg, end)
        n = c['t'].size
        counts[cycle] = n
        if n == 0:
            continue
        early, late = redate(c, space, cycle)
        c['basin'] = basins(c['lon'], c['lat'])
        out = out_path(root, space, cycle, run)
        refs = 'NOAA-NASA Joint Archive (NNJA), ' + ', '.join(
            sorted({f'{bucket.URL}/{bucket.PREFIX}/{s["source"]}/' for _, s, _ in mine}))
        ioda.write(out, c, space.kind, space.name, used, refs)
        msg = f'{os.path.relpath(out, root)}: {n} obs'
        if early or late:
            msg += (f' ({n - early - late} in window, {early} re-dated from before, '
                    f'{late} from after), ObsError {c["err"].min():.2f}-{c["err"].max():.2f}')
        log.info(msg)
    return counts


def parse_day(s):
    return datetime.strptime(s, '%Y%m%d')
