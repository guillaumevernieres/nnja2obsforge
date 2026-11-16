"""NNJA bucket access: S3 listing, file discovery from mapping.yaml sources, downloads.

The bucket is public (anonymous HTTPS). Downloading needs internet: login or u1-service
nodes, not compute nodes. Files are mirrored under <cache>/<bucket key>.
"""

import concurrent.futures
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

URL = 'https://noaa-reanalyses-pds.s3.amazonaws.com'
PREFIX = 'observations/reanalysis'
_S3 = '{http://s3.amazonaws.com/doc/2006-03-01/}'
# file time in a basename: 20230101.T060000Z, 20230101.T06z, 20240101T06, 2024-01-01T03,
# 20150401.T120000, or a bare date (adt_n1_20020828_iodav2.nc)
_TIME = re.compile(r'(?<!\d)(\d{4})-?(\d{2})-?(\d{2})(?:[._]?T(\d{2})\d*)?(?!\d)')


def file_time(name):
    """Time stamp of a file name (last date in the basename), None if there is none."""
    m = None
    for m in _TIME.finditer(os.path.basename(name)):
        pass
    if m is None:
        return None
    y, mo, d, h = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(h or 0))
    except ValueError:
        return None


def list_s3(prefix):
    """Every key below prefix/ (recursive), following S3 pagination."""
    keys, token = [], None
    while True:
        q = {'list-type': '2', 'prefix': prefix.rstrip('/') + '/'}
        if token:
            q['continuation-token'] = token
        with urllib.request.urlopen(f'{URL}/?{urllib.parse.urlencode(q)}', timeout=60) as r:
            root = ET.fromstring(r.read())
        keys += [c.find(_S3 + 'Key').text for c in root.findall(_S3 + 'Contents')]
        token = root.findtext(_S3 + 'NextContinuationToken')
        if not token:
            return keys


def list_cache(cache, prefix):
    """Every cached key below prefix/ (complete files only)."""
    top = os.path.join(cache, prefix)
    out = []
    for d, _, files in os.walk(top):
        out += [os.path.relpath(os.path.join(d, f), cache) for f in files
                if not f.endswith(('.part', '.tmp'))]
    return out


def months(beg, end):
    """(year, month) of every month touching [beg, end]."""
    y, m, out = beg.year, beg.month, []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class File:
    """One NNJA file: bucket key, nominal time and the span its obs may cover."""

    def __init__(self, key, t, cover):
        self.key, self.t = key, t
        self.beg = t + timedelta(hours=cover[0])
        self.end = t + timedelta(hours=cover[1])

    def overlaps(self, beg, end, closed=False):
        return (self.beg <= end if closed else self.beg < end) and self.end >= beg

    def __repr__(self):
        return f'File({self.key})'


def discover(source, beg, end, lister):
    """Files of one mapping source whose cover overlaps [beg, end).

    source: dict with 'source', 'files' (regexes, priority order), 'cover' [h0, h1].
    lister: prefix -> list of keys (list_s3, or list_cache bound to a cache dir).
    For each file time only the matches of the first pattern that has one are kept.
    """
    cover = source.get('cover', [-3, 3])
    pats = [re.compile(p) for p in source.get('files', ['.*'])]
    lo = beg - timedelta(hours=cover[1])
    hi = end - timedelta(hours=cover[0])
    best = {}
    for y, m in months(lo, hi):
        mdir = f"{PREFIX}/{source['source'].strip('/')}/{y:04d}/{m:02d}"
        for k in lister(mdir):
            rel = k[len(mdir) + 1:] if k.startswith(mdir + '/') else k.split(f'/{m:02d}/', 1)[-1]
            rank = next((i for i, p in enumerate(pats) if p.search(rel)), None)
            if rank is None:
                continue
            t = file_time(k)
            if t is None:
                continue
            f = File(k, t, cover)
            if not f.overlaps(beg, end):
                continue
            # one key per (time, rank); per time keep the best rank (several keys may share
            # a time, e.g. one mooring file per site)
            best.setdefault(t, {}).setdefault(rank, []).append(f)
    out = []
    for t in sorted(best):
        out += best[t][min(best[t])]
    return out


def download(key, path, timeout=120):
    """'ok', 'missing' (not in the bucket) or 'error'; one retry on errors."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(f'{URL}/{urllib.parse.quote(key)}', timeout=timeout) as r, \
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
            log.warning('%s: HTTP %s (attempt %d)', key, e.code, attempt)
        except (urllib.error.URLError, OSError) as e:
            log.warning('%s: %s (attempt %d)', key, e, attempt)
        if os.path.exists(tmp):
            os.remove(tmp)
    return 'error'


def fetch(keys, cache, workers=16):
    """Download the keys not already in the cache. Returns {'ok', 'missing', 'error'} counts."""
    jobs = {k: os.path.join(cache, k) for k in keys
            if not os.path.exists(os.path.join(cache, k))}
    log.info('%d files to fetch into %s (%d already cached)', len(jobs), cache,
             len(set(keys)) - len(jobs))
    count = {'ok': 0, 'missing': 0, 'error': 0}
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for r in pool.map(lambda kv: download(*kv), jobs.items()):
            count[r] += 1
    log.info('fetched %(ok)d, not in bucket %(missing)d, failed %(error)d', count)
    return count
