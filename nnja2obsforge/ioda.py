"""Read NNJA files (IODA v2/v3, PMEL moorings) into columns; write obsForge-style files.

Columns are numpy arrays of equal length:
  t      int64/float seconds since 1970-01-01
  lat, lon, y, err  float64
  qc     int32 NNJA PreQC (PMEL: 0 for quality 1-3, else the PMEL code)
  depth  float64 (insitu only, may be absent)
  station int32 (moorings: WMO id, may be absent)
"""

import os
import re
from datetime import datetime

import netCDF4 as nc
import numpy as np

EPOCH = datetime(1970, 1, 1)
FILL = {'f4': np.float32(-3.3687953e+38), 'i4': np.int32(-2147483643),
        'i8': np.int64(-9223372036854775801)}   # IODA defaults, as in obsForge files
_UNITS = re.compile(r'seconds since (\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})')


def _f(v):
    return np.ma.filled(np.ma.asarray(v[:]).astype(np.float64), np.nan)


def _epoch_shift(units):
    """Seconds from 1970-01-01 to the reference date of 'seconds since ...' units."""
    m = _UNITS.search(units or '')
    if not m:
        return 0
    ref = datetime.strptime(' '.join(m.groups()), '%Y-%m-%d %H:%M:%S')
    return int((ref - EPOCH).total_seconds())


def _find(group, names):
    for n in names:
        if n in group.variables:
            return group.variables[n]
    return None


def read_ioda(path, aliases, err_default):
    """One NNJA IODA file (v2: nlocs + MetaData/datetime strings; v3: Location +
    MetaData/dateTime) -> columns, or None when it has none of the aliases."""
    with nc.Dataset(path) as ds:
        if 'ObsValue' not in ds.groups:
            return None
        yv = _find(ds['ObsValue'], aliases)
        if yv is None:
            return None
        name = yv.name
        meta = ds['MetaData']
        if 'dateTime' in meta.variables:
            v = meta['dateTime']
            t = _f(v) + _epoch_shift(getattr(v, 'units', ''))
        else:
            s = np.asarray(meta['datetime'][:]).astype(str)
            s = np.char.rstrip(s, 'Z')
            t = (np.array(s, dtype='datetime64[s]').astype(np.int64)).astype(np.float64)
        out = {'t': t, 'lat': _f(meta['latitude']), 'lon': _f(meta['longitude']), 'y': _f(yv)}
        n = out['y'].size
        ev = ds['ObsError'].variables.get(name) if 'ObsError' in ds.groups else None
        out['err'] = _f(ev) if ev is not None else np.full(n, np.nan)
        out['err'] = np.where(np.isfinite(out['err']), out['err'], err_default)
        qv = ds['PreQC'].variables.get(name) if 'PreQC' in ds.groups else None
        out['qc'] = (np.ma.filled(qv[:], 0).astype(np.int32) if qv is not None
                     else np.zeros(n, np.int32))
        if 'depth' in meta.variables:
            out['depth'] = _f(meta['depth'])
    return out


def read_pmel(path, aliases, err_default):
    """One GTMBA (TAO/TRITON, PIRATA, RAMA) PMEL file -> columns.

    The variable is the one with generic_name temp (T, SST) or sal (S, SSS), chosen from
    the aliases (temperature-like -> temp). PreQC is 0 for PMEL quality 1-3 (highest,
    default, adjusted), the PMEL code otherwise.
    """
    gen = 'sal' if any('alin' in a for a in aliases) else 'temp'
    with nc.Dataset(path) as ds:
        vs = [v for v in ds.variables.values() if getattr(v, 'generic_name', '') == gen]
        if not vs:
            return None
        v = vs[0]
        vname, code = v.name.split('_')
        qn = f'Q{vname}_{5000 + int(code)}'         # T_20 -> QT_5020, S_41 -> QS_5041
        y = np.ma.filled(v[:][:, :, 0, 0].astype(np.float64), np.nan)
        q = (np.ma.filled(ds[qn][:][:, :, 0, 0], 0).astype(np.int32) if qn in ds.variables
             else np.full(y.shape, 2, np.int32))
        tv = ds['time']
        t = np.array([(x - EPOCH).total_seconds() for x in nc.num2date(
            tv[:], tv.units, getattr(tv, 'calendar', 'standard'),
            only_use_cftime_datetimes=False, only_use_python_datetimes=True)])
        z = np.asarray(ds['depth'][:], np.float64)
        lat, lon = float(ds['lat'][0]), float(ds['lon'][0])
        wmo = str(getattr(ds, 'wmo_platform_code', ''))
    tt, zz = np.meshgrid(t, z, indexing='ij')
    ok = np.isfinite(y) & (np.abs(y) < 1.0e10)
    n = int(ok.sum())
    return {'t': tt[ok], 'lat': np.full(n, lat), 'lon': np.full(n, lon), 'y': y[ok],
            'err': np.full(n, float(err_default)), 'depth': zz[ok],
            'qc': np.where(np.isin(q[ok], (1, 2, 3)), 0, q[ok]).astype(np.int32),
            'station': np.full(n, int(wmo) if wmo.isdigit() else FILL['i4'], np.int32)}


READERS = {'ioda': read_ioda, 'pmel': read_pmel}


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


# obsForge Rads2Ioda altimeterMap; missions not in it get 0, as in obsForge
MISSION = {'3a': 1, '3b': 2, 'j1': 3, 'j2': 4, 'j3': 5, 'c2': 6, 'sa': 7}
MISSION_INDEX = ('CRYOSAT2 = 6 JASON-1 = 3 JASON-2 = 4 JASON-3 = 5 SARAL = 7 '
                 'SNTNL-3A = 1 SNTNL-3B = 2 ')


def write(path, c, kind, space, sources, references):
    """One obsForge-style file (groups, names, types, IODA fills, zlib 6, one chunk).

    c: columns with t (s since 1970, re-dated), t0 (original time), lat, lon, y, err,
    basin, optional depth/station. kind: mapping kind (var, epoch, meta).
    """
    n = c['t'].size
    var = kind['var']
    ref = datetime.strptime(kind['epoch'].rstrip('Z'), '%Y-%m-%dT%H:%M:%S')
    shift = int((EPOCH - ref).total_seconds())
    tmp = path + '.tmp'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with nc.Dataset(tmp, 'w', format='NETCDF4') as ds:
        ds.setncattr('_ioda_layout', 'ObsGroup')
        ds.setncattr('_ioda_layout_version', np.int32(0))
        ds.setncattr('obs_source_files', sources)
        if kind['meta'] == 'adt':
            ds.setncattr('mission_index', MISSION_INDEX)
        ds.setncattr('references', references)
        ds.createDimension('Location', n)
        loc = ds.createVariable('Location', 'i4', ('Location',), chunksizes=(max(n, 1),))
        loc.setncattr('suggested_chunk_dim', np.int64(n))
        loc[:] = np.zeros(n, np.int32)

        def put(name, typ, data, **atts):
            v = ds.createVariable(name, typ, ('Location',), zlib=True, complevel=6,
                                  chunksizes=(max(n, 1),), fill_value=FILL[typ])
            for a, x in atts.items():
                v.setncattr(a, x)
            v[:] = data

        def missing(typ):
            return np.full(n, FILL[typ])

        units = f"seconds since {ref:%Y-%m-%dT%H:%M:%S}Z"
        t = np.round(c['t']).astype(np.int64) + shift
        t0 = np.round(c['t0']).astype(np.int64) + shift
        meta = kind['meta']
        if meta == 'adt':
            code = space.rsplit('_', 1)[-1]
            put('MetaData/cycle', 'i4', missing('i4'))
            put('MetaData/dateTime', 'i8', t, units=units)
            put('MetaData/latitude', 'f4', c['lat'].astype(np.float32))
            put('MetaData/longitude', 'f4', c['lon'].astype(np.float32))
            put('MetaData/mdt', 'f4', missing('f4'))
            put('MetaData/mission', 'i4', np.full(n, MISSION.get(code, 0), np.int32))
            put('MetaData/oceanBasin', 'i4', c['basin'].astype(np.int32))
            put('MetaData/originalDateTime', 'i8', t0)
        elif meta == 'insitu':
            put('MetaData/dateTime', 'i8', t, units=units)
            depth = c.get('depth')
            put('MetaData/depth', 'f4', (depth if depth is not None
                                         else np.zeros(n)).astype(np.float32))
            put('MetaData/latitude', 'f4', c['lat'].astype(np.float32))
            put('MetaData/longitude', 'f4', c['lon'].astype(np.float32))
            put('MetaData/oceanBasin', 'i4', c['basin'].astype(np.int32))
            put('MetaData/originalDateTime', 'i8', t0)
            put('MetaData/rcptdateTime', 'i4', missing('i4'))
            st = c.get('station')
            put('MetaData/stationID', 'i4', st.astype(np.int32) if st is not None
                else missing('i4'))
        else:
            put('MetaData/dateTime', 'i8', t, units=units)
            put('MetaData/latitude', 'f4', c['lat'].astype(np.float32))
            put('MetaData/longitude', 'f4', c['lon'].astype(np.float32))
            put('MetaData/oceanBasin', 'i4', c['basin'].astype(np.int32))
        put(f'ObsError/{var}', 'f4', c['err'].astype(np.float32))
        put(f'ObsValue/{var}', 'f4', c['y'].astype(np.float32))
        put(f'PreQC/{var}', 'i4', np.zeros(n, np.int32))
    os.replace(tmp, path)
