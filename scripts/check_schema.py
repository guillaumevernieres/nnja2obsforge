#!/usr/bin/env python3
"""Compare nnja2obsforge files with obsForge files of the same kind: variables, types,
fill values, dateTime units; plus basic sanity (dateTime in the cycle window, ranges).

usage: check_schema.py DIR [DIR ...] [--ref REFCYCLEDIR]
  DIR     a cycle directory, e.g. obsforge/gdas.20230101/00 (or any parent: searched)
  --ref   obsForge cycle with reference files
          (default /scratch3/NCEPDEV/da/common_obsForge/gdas.20261016/00)
"""

import argparse
import glob
import os
import re
from datetime import datetime, timedelta

import netCDF4 as nc
import numpy as np

REF = '/scratch3/NCEPDEV/da/common_obsForge/gdas.20261016/00'
# our space -> obsForge reference file stem (by kind)
REFS = [(r'^rads_adt_', 'rads_adt_j3'), (r'^insitu_temp_profile_', 'insitu_temp_profile_argo'),
        (r'^insitu_salt_profile_', 'insitu_salt_profile_argo'),
        (r'^insitu_temp_surface_', 'insitu_temp_surface_drifter'),
        (r'^insitu_salt_surface_', 'insitu_salt_surface_trkob'), (r'^sst_', 'sst_viirs_n20_l3u'),
        (r'^sss_', 'sss_smap_l2'), (r'^icec_', 'icec_amsr3_north')]


def schema(path):
    out = {}
    with nc.Dataset(path) as ds:
        for g in ds.groups.values():
            for v in g.variables.values():
                out[f'{g.name}/{v.name}'] = (str(v.dtype), v.getncattr('_FillValue').item()
                                             if '_FillValue' in v.ncattrs() else None,
                                             getattr(v, 'units', ''))
    return out


def find_ref(ref, space):
    for pat, stem in REFS:
        if re.search(pat, space):
            hits = glob.glob(f'{ref}/ocean/**/gdas.t??z.{stem}.nc', recursive=True)
            hits = [h for h in hits if os.path.exists(h)]
            return hits[0] if hits else None
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--ref', default=REF)
    args = ap.parse_args()
    nbad = 0
    files = sorted({f for d in args.dirs
                    for f in glob.glob(f'{d}/**/*.t??z.*.nc', recursive=True)})
    for f in files:
        m = re.search(r'\.(\d{8})/(\d{2})/ocean/[^/]+/\w+\.t\d\dz\.(\w+)\.nc$', f)
        if not m:
            continue
        cycle = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H')
        space = m.group(3)
        problems = []
        mine = schema(f)
        ref = find_ref(args.ref, space)
        if ref:
            theirs = schema(ref)
            if set(mine) != set(theirs):
                problems.append(f'vars +{sorted(set(mine) - set(theirs))} '
                                f'-{sorted(set(theirs) - set(mine))}')
            for k in set(mine) & set(theirs):
                if mine[k] != theirs[k]:
                    problems.append(f'{k}: {mine[k]} vs {theirs[k]}')
        with nc.Dataset(f) as ds:
            v = ds['MetaData/dateTime']
            units = v.units.replace('seconds since ', '').rstrip('Z')
            ref0 = datetime.strptime(units, '%Y-%m-%dT%H:%M:%S')
            t = v[:].astype(np.int64)
            wb = (cycle - timedelta(hours=3) - ref0).total_seconds()
            we = (cycle + timedelta(hours=3) - ref0).total_seconds()
            if t.size and (t.min() < wb or t.max() > we):
                problems.append(f'dateTime outside [T-3h, T+3h]: '
                                f'{ref0 + timedelta(seconds=int(t.min()))} .. '
                                f'{ref0 + timedelta(seconds=int(t.max()))}')
            var = [k for k in ds['ObsValue'].variables][0]
            y = ds[f'ObsValue/{var}'][:]
            e = ds[f'ObsError/{var}'][:]
            lat, lon = ds['MetaData/latitude'][:], ds['MetaData/longitude'][:]
            if np.ma.count_masked(y) or np.ma.count_masked(e):
                problems.append('masked ObsValue/ObsError')
            if (np.abs(lat) > 90).any() or (lon < -180).any() or (lon >= 180).any():
                problems.append('lat/lon out of range')
            if (e <= 0).any():
                problems.append(f'ObsError <= 0 ({int((e <= 0).sum())})')
            summary = (f'n={t.size} {var} [{y.min():.3g}, {y.max():.3g}] '
                       f'err [{e.min():.3g}, {e.max():.3g}]')
        status = 'OK ' if not problems else 'BAD'
        nbad += bool(problems)
        print(f'{status} {cycle:%Y%m%d%H} {space}: {summary}'
              f'{"" if ref else " (no obsForge reference)"}')
        for p in problems:
            print('     ', p)
    print(f'{len(files)} files, {nbad} with problems')
    return int(nbad > 0)


if __name__ == '__main__':
    raise SystemExit(main())
