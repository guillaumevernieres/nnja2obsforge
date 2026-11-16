#!/usr/bin/env python3
"""NNJA GLORe along-track ADT -> obsForge RADS ADT files, gdas 00z cycles.

Source (public S3 bucket noaa-reanalyses-pds, 6-hourly files covering [T-3h, T+3h)):
  observations/reanalysis/adt/GLORe/<sat>/YYYY/MM/iodav3/adt.<code>.YYYYMMDD.THH0000Z.iodav3.nc
Output, laid out like common_obsForge:
  <root>/obsforge/gdas.YYYYMMDD/00/ocean/adt/gdas.t00z.rads_adt_<code>.nc

For the 00z cycle of day D the obs of [D - half_window, D + half_window] are kept and
re-dated into the 6 h window [D-3h, D+3h] as obsForge's Rads2Ioda::reDate does: an obs
before the window goes to window begin + 1 s, after it to window end - 1 s, and its
ObsError grows by error_ratio (m/day) times the time it was moved; originalDateTime keeps
the true time. oceanBasin is the RECCAP2 open_ocean region (nearest 1-degree cell, as
obsForge's OceanMask); mission uses obsForge's map; cycle and mdt are not in NNJA and are
written as missing.

usage: nnja_adt2obsforge.py --start 20230101 --end 20230131 [options]
Downloading needs internet (login or u1-service nodes); --no-fetch uses the cache only.
"""

import argparse
import concurrent.futures
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import netCDF4 as nc
import numpy as np

log = logging.getLogger('nnja_adt')

URL = 'https://noaa-reanalyses-pds.s3.amazonaws.com'
PREFIX = 'observations/reanalysis/adt/GLORe'
ROOT = os.path.dirname(os.path.abspath(__file__))
BASIN = ('/scratch3/NCEPDEV/da/Guillaume.Vernieres/runs/gfs-dev/mom6100_grid/'
         'RECCAP2_region_masks_all_v20221025.nc')
SATS = {'cryosat2': 'c2', 'jason2': 'j2', 'jason3': 'j3', 'saral': 'sa',
        'sentinel3a': '3a', 'sentinel3b': '3b', 'sentinel6a': '6a', 'swot': 'sw'}
# obsForge Rads2Ioda altimeterMap; missions not in it get 0, as in obsForge
MISSION = {'3a': 1, '3b': 2, 'j2': 4, 'j3': 5, 'c2': 6, 'sa': 7}
MISSION_INDEX = ('CRYOSAT2 = 6 JASON-1 = 3 JASON-2 = 4 JASON-3 = 5 SARAL = 7 '
                 'SNTNL-3A = 1 SNTNL-3B = 2 ')
EPOCH_IN = datetime(1970, 1, 1)
EPOCH_OUT = datetime(1858, 11, 17)            # obsForge RADS reference date
SHIFT = int((EPOCH_IN - EPOCH_OUT).total_seconds())
VAR = 'absoluteDynamicTopography'
FILL = {'f4': np.float32(-3.3687953e+38), 'i4': np.int32(-2147483643),
        'i8': np.int64(-9223372036854775801)}   # IODA defaults, as in obsForge files
STEP = timedelta(hours=6)
_S3 = '{http://s3.amazonaws.com/doc/2006-03-01/}'


def key(sat, t):
    return (f'{PREFIX}/{sat}/{t:%Y}/{t:%m}/iodav3/'
            f'adt.{SATS[sat]}.{t:%Y%m%d}.T{t:%H}0000Z.iodav3.nc')


def file_times(cycle, half):
    """6-hourly file times whose [T-3h, T+3h) overlaps [cycle - half, cycle + half]."""
    t, out = cycle - half, []
    while t <= cycle + half:
        out.append(t)
        t += STEP
    return out


def list_keys(prefix):
    """Keys directly under prefix/, following S3 pagination."""
    keys, token = [], None
    while True:
        q = {'list-type': '2', 'prefix': prefix + '/', 'delimiter': '/'}
        if token:
            q['continuation-token'] = token
        with urllib.request.urlopen(f'{URL}/?{urllib.parse.urlencode(q)}', timeout=60) as r:
            root = ET.fromstring(r.read())
        keys += [c.find(_S3 + 'Key').text for c in root.findall(_S3 + 'Contents')]
        token = root.findtext(_S3 + 'NextContinuationToken')
        if not token:
            return keys


def download(k, path, timeout=60):
    """'ok', 'missing' (not in the bucket) or 'error'; one retry on errors."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(f'{URL}/{k}', timeout=timeout) as r, \
                    open(tmp, 'wb') as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, path)
            return 'ok'
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return 'missing'
            log.warning('%s: HTTP %s (attempt %d)', k, e.code, attempt)
        except (urllib.error.URLError, OSError) as e:
            log.warning('%s: %s (attempt %d)', k, e, attempt)
        if os.path.exists(tmp):
            os.remove(tmp)
    return 'error'


def fetch(cache, sats, cycles, half, workers):
    """Download the files of every cycle not already in the cache. Returns the number of
    failed downloads."""
    want = {(s, t) for s in sats for c in cycles for t in file_times(c, half)}
    listed = {}
    for d in sorted({os.path.dirname(key(s, t)) for s, t in want}):
        listed[d] = set(list_keys(d))
    jobs, absent, ncached = {}, {}, 0
    for s, t in sorted(want):
        k = key(s, t)
        if os.path.exists(os.path.join(cache, k)):
            ncached += 1
        elif k in listed[os.path.dirname(k)]:
            jobs[k] = os.path.join(cache, k)
        else:
            absent[s] = absent.get(s, 0) + 1
    for s, n in sorted(absent.items()):
        log.info('%s: %d of the 6-hourly files are not in the bucket', s, n)
    log.info('%d files to fetch into %s (%d already cached)', len(jobs), cache, ncached)
    count = {'ok': 0, 'missing': 0, 'error': 0}
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for r in pool.map(lambda kv: download(*kv), jobs.items()):
            count[r] += 1
    log.info('fetched %(ok)d, not in bucket %(missing)d, failed %(error)d', count)
    return count['error']


def read(paths):
    """Concatenated (t [s since 1970], lat, lon, adt, err) of the files, PreQC == 0 only."""
    cols = [[] for _ in range(5)]
    for p in paths:
        with nc.Dataset(p) as ds:
            t = np.ma.filled(ds['MetaData/dateTime'][:].astype(np.float64), np.nan)
            lat = np.ma.filled(ds['MetaData/latitude'][:].astype(np.float64), np.nan)
            lon = np.ma.filled(ds['MetaData/longitude'][:].astype(np.float64), np.nan)
            y = np.ma.filled(ds[f'ObsValue/{VAR}'][:].astype(np.float64), np.nan)
            e = np.ma.filled(ds[f'ObsError/{VAR}'][:].astype(np.float64), np.nan)
            q = np.ma.filled(ds[f'PreQC/{VAR}'][:].astype(np.float64), np.nan)
        ok = q == 0
        for c, x in zip(cols, (t, lat, lon, y, e)):
            c.append(x[ok])
    return [np.concatenate(c) for c in cols]


class Basins:
    """RECCAP2 open_ocean region of the nearest 1-degree cell (obsForge OceanMask)."""

    def __init__(self, path):
        with nc.Dataset(path) as ds:
            self.mask = np.ma.filled(ds['open_ocean'][:], 0).astype(np.int32)

    def __call__(self, lon, lat):
        x = np.where(lon >= 0.0, lon, lon + 360.0) - 0.5
        i = np.floor(x + 0.5).astype(np.int64) % self.mask.shape[1]
        j = np.clip(np.floor(lat + 89.5 + 0.5).astype(np.int64), 0, self.mask.shape[0] - 1)
        return self.mask[j, i]


def write(path, t, t0, lat, lon, y, err, code, basin, sources):
    """One obsForge RADS ADT file (same groups, names, types, fills and compression)."""
    n = t.size
    tmp = path + '.tmp'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with nc.Dataset(tmp, 'w', format='NETCDF4') as ds:
        ds.setncattr('_ioda_layout', 'ObsGroup')
        ds.setncattr('_ioda_layout_version', np.int32(0))
        ds.setncattr('obs_source_files', sources)
        ds.setncattr('mission_index', MISSION_INDEX)
        ds.setncattr('references', f'NOAA-NASA Joint Archive (NNJA), {URL}/{PREFIX}/')
        ds.createDimension('Location', n)
        loc = ds.createVariable('Location', 'i4', ('Location',), chunksizes=(n,))
        loc.setncattr('suggested_chunk_dim', np.int64(n))
        loc[:] = np.zeros(n, np.int32)

        def var(name, typ, data, **atts):
            v = ds.createVariable(name, typ, ('Location',), zlib=True, complevel=6,
                                  chunksizes=(n,), fill_value=FILL[typ])
            for a, x in atts.items():
                v.setncattr(a, x)
            v[:] = data
        units = f'seconds since {EPOCH_OUT:%Y-%m-%dT%H:%M:%S}Z'
        var('MetaData/cycle', 'i4', np.full(n, FILL['i4']))
        var('MetaData/dateTime', 'i8', t + SHIFT, units=units)
        var('MetaData/latitude', 'f4', lat.astype(np.float32))
        var('MetaData/longitude', 'f4', lon.astype(np.float32))
        var('MetaData/mdt', 'f4', np.full(n, FILL['f4']))
        var('MetaData/mission', 'i4', np.full(n, MISSION.get(code, 0), np.int32))
        var('MetaData/oceanBasin', 'i4', basin.astype(np.int32))
        var('MetaData/originalDateTime', 'i8', t0 + SHIFT)
        var(f'ObsError/{VAR}', 'f4', err.astype(np.float32))
        var(f'ObsValue/{VAR}', 'f4', y.astype(np.float32))
        var(f'PreQC/{VAR}', 'i4', np.zeros(n, np.int32))
    os.replace(tmp, path)


def cycle_file(root, sat, cycle, half, ratio, basins, cache):
    """Write the file of one satellite and cycle; returns its obs count (0: no file)."""
    code = SATS[sat]
    paths = [p for p in (os.path.join(cache, key(sat, t)) for t in file_times(cycle, half))
             if os.path.exists(p)]
    if not paths:
        return 0
    t, lat, lon, y, err = read(paths)
    c = int((cycle - EPOCH_IN).total_seconds())
    h = int(half.total_seconds())
    ok = (np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon) & np.isfinite(y)
          & (y > -4.0) & (y < 4.0) & (t >= c - h) & (t <= c + h))
    t, lat, lon, y, err = (x[ok] for x in (t, lat, lon, y, err))
    err = np.where(np.isfinite(err), err, 0.1)
    t = np.round(t).astype(np.int64)
    lon = (lon + 180.0) % 360.0 - 180.0
    # files do not overlap, but keep one of any repeated record; sort by time
    _, first = np.unique(np.stack([t, np.round(lat * 1e5).astype(np.int64),
                                   np.round(lon * 1e5).astype(np.int64)], axis=1),
                         axis=0, return_index=True)
    first = first[np.argsort(t[first], kind='stable')]
    t, lat, lon, y, err = (x[first] for x in (t, lat, lon, y, err))
    if t.size == 0:
        return 0
    # re-date into the 6 h window, as Rads2Ioda::reDate
    wb, we = c - 3 * 3600, c + 3 * 3600
    t0 = t.copy()
    early, late = t < wb, t > we
    err = err + ratio / 86400.0 * (np.where(early, wb - t, 0) + np.where(late, t - we, 0))
    t = np.where(early, wb + 1, np.where(late, we - 1, t))
    out = os.path.join(root, f'gdas.{cycle:%Y%m%d}', f'{cycle:%H}', 'ocean', 'adt',
                       f'gdas.t{cycle:%H}z.rads_adt_{code}.nc')
    write(out, t, t0, lat, lon, y, err, code, basins(lon, lat),
          [os.path.basename(p) for p in paths])
    log.info('%s: %d obs (%d in window, %d re-dated from before, %d from after), '
             'ObsError %.2f-%.2f m', os.path.relpath(out, root), t.size,
             t.size - early.sum() - late.sum(), early.sum(), late.sum(), err.min(), err.max())
    return t.size


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--start', required=True, help='first cycle day, YYYYMMDD')
    ap.add_argument('--end', required=True, help='last cycle day, YYYYMMDD')
    ap.add_argument('--root', default=ROOT, help='holds cache/ and obsforge/ (default: '
                    'this script\'s directory)')
    ap.add_argument('--half-window', type=float, default=48.0,
                    help='hours of obs kept on each side of the cycle time (default 48)')
    ap.add_argument('--error-ratio', type=float, default=1.0,
                    help='ObsError added per day of re-dating, m/day (default 1.0)')
    ap.add_argument('--sats', nargs='+', default=list(SATS), choices=list(SATS))
    ap.add_argument('--basin-file', default=BASIN)
    ap.add_argument('--workers', type=int, default=16, help='parallel downloads')
    ap.add_argument('--no-fetch', action='store_true', help='use the cache only')
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s')

    first = datetime.strptime(args.start, '%Y%m%d')
    last = datetime.strptime(args.end, '%Y%m%d')
    cycles = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    half = timedelta(hours=args.half_window)
    cache = os.path.join(args.root, 'cache')
    out = os.path.join(args.root, 'obsforge')
    if not args.no_fetch and fetch(cache, args.sats, cycles, half, args.workers):
        sys.exit('some downloads failed; rerun to retry them')
    basins = Basins(args.basin_file)
    for cycle in cycles:
        counts = {SATS[s]: cycle_file(out, s, cycle, half, args.error_ratio, basins, cache)
                  for s in args.sats}
        log.info('gdas.%s 00z: %s', f'{cycle:%Y%m%d}',
                 ', '.join(f'{k} {v}' for k, v in counts.items() if v))


if __name__ == '__main__':
    main()
