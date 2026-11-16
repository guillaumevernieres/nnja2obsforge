"""Checks of one output file and comparisons with obsForge files (used by check.py).

Each check returns metrics (dict) and issues: list of (level, message), level FAIL or WARN.
"""

import glob
import os
import re
import netCDF4 as nc
import numpy as np

from . import ioda

# physical ranges (WARN outside) and ObsError caps (FAIL above), per kind
PHYS = {'adt': (-2.5, 2.5), 'insitu_temp_profile': (-2.5, 36.0),
        'insitu_salt_profile': (2.0, 42.0), 'insitu_temp_surface': (-2.5, 36.0),
        'insitu_salt_surface': (2.0, 42.0), 'sst': (-2.5, 36.0), 'sss': (2.0, 42.0),
        'icec': (0.0, 1.0), 'icefb': (-0.5, 2.5)}
ERRCAP = {'adt': 3.0, 'insitu_temp_profile': 5.0, 'insitu_salt_profile': 2.0,
          'insitu_temp_surface': 5.0, 'insitu_salt_surface': 2.0, 'sst': 5.0, 'sss': 5.0,
          'icec': 1.0, 'icefb': 1.0}
MAD_K = 8.0          # robust outlier threshold (ADT), in (1.4826 MAD) units
OUTLIER_WARN = 0.005
LAND_WARN = 0.05
# WOA23 check (in situ, SST, SSS): z = (O - WOA mean) / max(WOA std, floor)
WOA_URL = ('https://www.ncei.noaa.gov/thredds-ocean/fileServer/woa23/DATA/{long}/netcdf/'
           'decav91C0/1.00/woa23_decav91C0_{v}{mm:02d}_01.nc')
WOA_FLOOR = {'t': 0.5, 's': 0.1}
WOA_Z = 6.0
WOA_FRAC_WARN = 0.01
WOA_BIAS_WARN = {'t': 1.0, 's': 0.3}

# obsForge reference file stem for the schema comparison, by space-name pattern
SCHEMA_REFS = [(r'^rads_adt_', 'rads_adt_j3'),
               (r'^insitu_temp_profile_', 'insitu_temp_profile_argo'),
               (r'^insitu_salt_profile_', 'insitu_salt_profile_argo'),
               (r'^insitu_temp_surface_', 'insitu_temp_surface_drifter'),
               (r'^insitu_salt_surface_', 'insitu_salt_surface_trkob'),
               (r'^sst_', 'sst_viirs_n20_l3u'), (r'^sss_', 'sss_smap_l2'),
               (r'^icec_', 'icec_amsr3_north')]
SCHEMA_REF_CYCLE = '/scratch3/NCEPDEV/da/common_obsForge/gdas.20261016/00'

# NNJA space -> (obsForge sub-directory, obsForge name) for the same-cycle comparison
PAIRS = {'insitu_temp_profile_argo': ('insitu', 'insitu_temp_profile_argo'),
         'insitu_salt_profile_argo': ('insitu', 'insitu_salt_profile_argo'),
         'insitu_temp_profile_glider': ('insitu', 'insitu_temp_profile_glider'),
         'insitu_salt_profile_glider': ('insitu', 'insitu_salt_profile_glider'),
         'icec_nsidc_north': ('icec', 'icec_amsr2_north'),
         'icec_nsidc_south': ('icec', 'icec_amsr2_south')}
for _c in ('3a', '3b', '6a', 'c2', 'j3', 'sa', 'sw'):
    PAIRS[f'rads_adt_{_c}'] = ('adt_old', f'rads_adt_{_c}')
# collocation tolerances: horizontal km, seconds, metres; difference WARN level
MATCH = {'insitu_temp_profile': (5.0, 3600.0, 2.0, 0.1),
         'insitu_salt_profile': (5.0, 3600.0, 2.0, 0.05),
         'adt': (7.0, 60.0, None, 0.05)}
R_EARTH = 6371.0


def schema(path):
    out = {}
    with nc.Dataset(path) as ds:
        for g in ds.groups.values():
            for v in g.variables.values():
                fill = v.getncattr('_FillValue').item() if '_FillValue' in v.ncattrs() else None
                out[f'{g.name}/{v.name}'] = (str(v.dtype), fill, getattr(v, 'units', ''))
    return out


def schema_ref(space, ref_cycle=SCHEMA_REF_CYCLE):
    for pat, stem in SCHEMA_REFS:
        if re.search(pat, space):
            hits = [h for h in glob.glob(f'{ref_cycle}/ocean/**/gdas.t??z.{stem}.nc',
                                         recursive=True) if os.path.exists(h)]
            return hits[0] if hits else None
    return None


def compare_schema(path, ref):
    issues = []
    mine, theirs = schema(path), schema(ref)
    if set(mine) != set(theirs):
        issues.append(('FAIL', f'variables +{sorted(set(mine) - set(theirs))} '
                               f'-{sorted(set(theirs) - set(mine))}'))
    for k in sorted(set(mine) & set(theirs)):
        if mine[k][:2] != theirs[k][:2]:
            issues.append(('FAIL', f'{k}: dtype/fill {mine[k][:2]} vs {theirs[k][:2]}'))
        if k == 'MetaData/dateTime' and mine[k][2] != theirs[k][2]:
            issues.append(('FAIL', f'dateTime units {mine[k][2]!r} vs {theirs[k][2]!r}'))
    return issues


def _epoch(units):
    from datetime import datetime
    return datetime.strptime(units.replace('seconds since ', '').rstrip('Z'),
                             '%Y-%m-%dT%H:%M:%S')


def load(path):
    """Columns of an obsForge-style file; times as datetime-free seconds since 1970."""
    with nc.Dataset(path) as ds:
        ds.set_auto_mask(False)
        meta = ds['MetaData']
        var = list(ds['ObsValue'].variables)[0]
        shift = (_epoch(meta['dateTime'].units) - ioda.EPOCH).total_seconds()
        c = {'var': var, 't': meta['dateTime'][:].astype(np.int64) + int(shift),
             'lat': meta['latitude'][:].astype(np.float64),
             'lon': meta['longitude'][:].astype(np.float64),
             'y': ds[f'ObsValue/{var}'][:].astype(np.float64),
             'err': ds[f'ObsError/{var}'][:].astype(np.float64),
             'qc': ds[f'PreQC/{var}'][:].astype(np.int64)}
        if 'originalDateTime' in meta.variables:
            c['t0'] = meta['originalDateTime'][:].astype(np.int64) + int(shift)
        if 'depth' in meta.variables:
            c['depth'] = meta['depth'][:].astype(np.float64)
        if 'oceanBasin' in meta.variables:
            c['basin'] = meta['oceanBasin'][:].astype(np.int64)
    for k in ('lat', 'lon', 'y', 'err', 'depth'):
        if k in c:
            c[k] = np.where(np.abs(c[k]) > 1e30, np.nan, c[k])     # IODA float fill
    return c


def robust_outliers(y, depth=None):
    """Number of |y - median| > MAD_K * 1.4826 MAD (per depth bin for profiles)."""
    if depth is None:
        groups = [np.ones(y.size, bool)]
    else:
        edges = np.r_[np.arange(0, 2000, 100), np.arange(2000, 12000, 500)]
        b = np.digitize(depth, edges)
        groups = [b == k for k in np.unique(b)]
    n = 0
    for g in groups:
        x = y[g]
        if x.size < 20:
            continue
        med = np.median(x)
        s = 1.4826 * np.median(np.abs(x - med))
        if s <= 0:
            continue
        n += int((np.abs(x - med) > MAD_K * s).sum())
    return n


class Woa:
    """WOA23 1-degree decav91C0 mean and standard deviation (monthly to 1500 m, annual
    below), downloaded once into <dir>."""

    def __init__(self, directory, offline=False):
        self.dir, self.offline, self.mem = directory, offline, {}

    def _file(self, v, mm):
        import urllib.error
        import urllib.request
        name = f'woa23_decav91C0_{v}{mm:02d}_01.nc'
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            if self.offline:
                return None
            os.makedirs(self.dir, exist_ok=True)
            url = WOA_URL.format(long='temperature' if v == 't' else 'salinity', v=v, mm=mm)
            try:
                urllib.request.urlretrieve(url, path + '.part')
            except (urllib.error.URLError, OSError) as e:
                print(f'WOA23 not available ({url}: {e}); climatology check skipped')
                self.offline = True
                return None
            os.replace(path + '.part', path)
        return path

    def fields(self, v, month):
        if (v, month) not in self.mem:
            out = []
            for mm in (month, 0):
                p = self._file(v, mm)
                if p is None:
                    self.mem[(v, month)] = None
                    return None
                with nc.Dataset(p) as ds:
                    out.append((np.asarray(ds['depth'][:], np.float64),
                                np.ma.filled(ds[f'{v}_an'][0].astype(np.float64), np.nan),
                                np.ma.filled(ds[f'{v}_sd'][0].astype(np.float64), np.nan)))
            self.mem[(v, month)] = out
        return self.mem[(v, month)]

    def __call__(self, v, month, lat, lon, depth):
        """(mean, std) at the obs (nearest 1-degree cell and standard level)."""
        f = self.fields(v, month)
        if f is None:
            return None, None
        j = np.clip(np.floor(lat + 90).astype(int), 0, 179)
        i = np.floor(lon + 180).astype(int) % 360
        mean = np.full(lat.size, np.nan)
        sd = np.full(lat.size, np.nan)
        for z, an, sdv in f:                       # monthly first, annual fills the rest
            k = np.clip(np.searchsorted(z, depth), 0, z.size - 1)
            km = np.clip(k - 1, 0, z.size - 1)
            k = np.where(np.abs(z[km] - depth) < np.abs(z[k] - depth), km, k)
            inside = depth <= z[-1] + 50
            todo = np.isnan(mean) & inside
            mean[todo] = an[k[todo], j[todo], i[todo]]
            sd[todo] = sdv[k[todo], j[todo], i[todo]]
        return mean, sd


def woa_check(c, kind, cycle, woa):
    """Metrics and issues of the comparison with WOA23."""
    v = 's' if ('salt' in kind or kind == 'sss') else 't'
    depth = c['depth'] if 'depth' in c else np.zeros(c['y'].size)
    mean, sd = woa(v, cycle.month, c['lat'], c['lon'], depth)
    if mean is None:
        return {}, []
    ok = np.isfinite(mean)
    m = {'woa_frac': float(ok.mean())}
    if ok.sum() < 10:
        return m, []
    d = c['y'][ok] - mean[ok]
    z = d / np.maximum(np.where(np.isfinite(sd[ok]), sd[ok], 0), WOA_FLOOR[v])
    m['woa_bias'] = float(np.median(d))
    m['woa_n_z6'] = int((np.abs(z) > WOA_Z).sum())
    issues = []
    frac = m['woa_n_z6'] / ok.sum()
    if frac > WOA_FRAC_WARN:
        issues.append(('WARN', f"{m['woa_n_z6']} obs ({100 * frac:.1f}%) > {WOA_Z:g} std "
                               'from WOA23'))
    if abs(m['woa_bias']) > WOA_BIAS_WARN[v]:
        issues.append(('WARN', f"median O-WOA23 {m['woa_bias']:.2f}"))
    return m, issues


def content(path, space, cycle, woa=None):
    """Invariants of one output file. Returns (metrics, issues, columns)."""
    kind = space.kind_name
    issues = []
    c = load(path)
    n = c['t'].size
    T = int((cycle - ioda.EPOCH).total_seconds())
    half = int(space.window['half'] * 3600)
    m = {'n': n}
    if n == 0:
        return m, [('FAIL', 'empty file')], c
    for k in ('lat', 'lon', 'y', 'err'):
        bad = int((~np.isfinite(c[k])).sum())
        if bad:
            issues.append(('FAIL', f'{bad} missing/non-finite {k}'))
    if (c['qc'] != 0).any():
        issues.append(('FAIL', f"{int((c['qc'] != 0).sum())} PreQC != 0"))
    if (np.abs(c['lat']) > 90).any() or (c['lon'] < -180).any() or (c['lon'] >= 180).any():
        issues.append(('FAIL', 'lat/lon out of range'))
    out = (c['t'] < T - 3 * 3600) | (c['t'] > T + 3 * 3600)
    if out.any():
        issues.append(('FAIL', f'{int(out.sum())} dateTime outside [T-3h, T+3h]'))
    if 't0' in c:
        far = (c['t0'] < T - half) | (c['t0'] > T + half)
        if far.any():
            issues.append(('FAIL', f'{int(far.sum())} originalDateTime outside [T-half, T+half]'))
        inside = (c['t0'] >= T - 3 * 3600) & (c['t0'] <= T + 3 * 3600)
        moved = inside & (c['t0'] != c['t'])
        if moved.any():
            issues.append(('FAIL', f'{int(moved.sum())} in-window obs with dateTime != '
                                   'originalDateTime'))
        m['redated'] = int((~inside).sum())
    if 'depth' in c:
        d = c['depth']
        bad = (~np.isfinite(d)) | (d < 0) | (d > 11000)
        if bad.any():
            issues.append(('FAIL', f'{int(bad.sum())} depth missing or outside [0, 11000] m'))
        m['depth_max'] = float(np.nanmax(d))
    e = c['err']
    m['err_min'], m['err_max'] = float(np.nanmin(e)), float(np.nanmax(e))
    if (e <= 0).any():
        issues.append(('FAIL', f'{int((e <= 0).sum())} ObsError <= 0'))
    if (e > ERRCAP[kind]).any():
        issues.append(('FAIL', f'{int((e > ERRCAP[kind]).sum())} ObsError > {ERRCAP[kind]}'))
    y = c['y']
    m['y_min'], m['y_max'] = float(np.nanmin(y)), float(np.nanmax(y))
    m['y_mean'], m['y_std'] = float(np.nanmean(y)), float(np.nanstd(y))
    lo, hi = PHYS[kind]
    nphys = int(((y < lo) | (y > hi)).sum())
    m['n_unphysical'] = nphys
    if nphys:
        issues.append(('WARN', f'{nphys} values outside the physical range [{lo}, {hi}]'))
    if kind == 'adt':
        nout = robust_outliers(y)
        m['n_outliers'] = nout
        if nout > OUTLIER_WARN * n:
            issues.append(('WARN', f'{nout} robust outliers ({100 * nout / n:.2f}%, '
                                   f'> {MAD_K} MAD)'))
    elif woa is not None and (kind.startswith('insitu') or kind in ('sst', 'sss')):
        wm, wi = woa_check(c, kind, cycle, woa)
        m.update(wm)
        issues += wi
    keys = [c['t0'] if 't0' in c else c['t'], np.round(c['lat'] * 1e5),
            np.round(c['lon'] * 1e5)]
    if 'depth' in c:
        keys.append(np.round(c['depth'] * 100))
    ndup = n - np.unique(np.stack(keys, 1).astype(np.int64), axis=0).shape[0]
    if ndup:
        issues.append(('FAIL', f'{ndup} duplicate records'))
    if 'basin' in c and not kind.startswith('ic'):
        land = float((c['basin'] == 0).mean())
        m['land_frac'] = land
        if land > LAND_WARN:
            issues.append(('WARN', f'{100 * land:.1f}% with oceanBasin 0 (land/none)'))
    return m, issues, c


def record_keys(c):
    """int64 rows identifying records (true time, position[, depth]) for overlap tests."""
    keys = [c['t0'] if 't0' in c else c['t'], np.round(c['lat'] * 1e5),
            np.round(c['lon'] * 1e5)]
    if 'depth' in c:
        keys.append(np.round(c['depth'] * 100))
    a = np.ascontiguousarray(np.stack(keys, 1).astype(np.int64))
    return a.view(np.dtype((np.void, a.dtype.itemsize * a.shape[1]))).ravel()


def _xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], 1) * R_EARTH


def collocate(a, b, kind, cycle):
    """Match each obs of a (NNJA) to b (obsForge) within the kind's tolerances, using the
    true times within [T-3h, T+3h]. Returns metrics."""
    from scipy.spatial import cKDTree
    dx, dt, dz, warn = MATCH[kind]
    T = int((cycle - ioda.EPOCH).total_seconds())

    def pick(c, t, pad):
        ok = (t >= T - 3 * 3600 - pad) & (t <= T + 3 * 3600 + pad) & np.isfinite(c['y'])
        return {k: v[ok] for k, v in c.items() if isinstance(v, np.ndarray)}, t[ok]

    # true times in the window (obsForge re-dates in situ obs from other days into the
    # window; its originalDateTime is sometimes a fill: then dateTime is used); the
    # reference is padded by the time tolerance
    a, ta = pick(a, a.get('t0', a['t']), 0)
    t0 = b.get('t0', b['t'])
    tref = np.where(np.abs(t0 - b['t']) < 30 * 86400, t0, b['t'])
    n_ref = int(pick(b, tref, 0)[1].size)
    b, tb = pick(b, tref, dt)
    m = {'n_nnja_6h': int(ta.size), 'n_ref_6h': n_ref}
    if ta.size == 0 or tb.size == 0:
        return m
    # chebyshev distance in scaled units: |dx|/tol, |dt|/tol, |dz|/tol all <= 1
    def pts(c, t):
        p = [_xyz(c['lat'], c['lon']) / dx, (t - T)[:, None] / dt]
        if dz is not None:
            p.append(c['depth'][:, None] / dz)
        return np.hstack(p)
    tree = cKDTree(pts(b, tb))
    d, i = tree.query(pts(a, ta), k=1, p=np.inf, distance_upper_bound=1.0)
    ok = np.isfinite(d)
    m['matched_frac'] = float(ok.mean())
    if ok.sum() >= 10:
        diff = a['y'][ok] - b['y'][i[ok]]
        med = float(np.median(diff))
        m['diff_median'] = med
        m['diff_rstd'] = float(1.4826 * np.median(np.abs(diff - med)))
        m['diff_absmax'] = float(np.abs(diff).max())
        m['diff_warn'] = warn
        m['_diff'] = diff
    return m


def boxes(a, b, cycle):
    """1-degree box means (sea ice): mean abs difference and correlation over common boxes."""
    def grid(c):
        j = np.clip(np.floor(c['lat'] + 90).astype(int), 0, 179)
        i = j * 360 + np.floor(c['lon'] + 180).astype(int) % 360
        s = np.bincount(i, c['y'], 180 * 360)
        n = np.bincount(i, minlength=180 * 360)
        return s, n
    sa, na = grid(a)
    sb, nb = grid(b)
    ok = (na > 0) & (nb > 0)
    m = {'n_nnja': int(a['y'].size), 'n_ref': int(b['y'].size), 'common_boxes': int(ok.sum())}
    if ok.sum() >= 10:
        ma, mb = sa[ok] / na[ok], sb[ok] / nb[ok]
        m['box_mean_absdiff'] = float(np.abs(ma - mb).mean())
        m['box_corr'] = float(np.corrcoef(ma, mb)[0, 1])
        m['_diff'] = ma - mb
    return m


def ref_path(ref_root, space_name, cycle, run='gdas'):
    sub, name = PAIRS[space_name]
    p = os.path.join(ref_root, f'{run}.{cycle:%Y%m%d}', f'{cycle:%H}', 'ocean', sub,
                     f'{run}.t{cycle:%H}z.{name}.nc')
    return p if os.path.exists(p) else None

