"""NNJA conventional marine BUFR dumps -> obsForge files, with obsForge itself.

NNJA keeps the NCEP surface-marine dumps (ships, drifting and moored buoys, C-MAN...):
  conv/convbufr/sfcshp/YYYY/MM/gdas.YYYYMMDD.tHHz.sfcshp.bufr_d.nr
obsForge's marine BUFR task (sorc/obsForge, MarineBufrObsPrep) splits an sfcshp dump into
its subsets (SfcShp: NC001102 -> dbuoyb, NC001103 -> mbuoyb, ...), runs its bufr2ioda
converters and concatenates the window with obsforge_obsprovider2ioda.x. This module
stages the NNJA dumps the way that task expects them,

  <work>/obsproc/gdas.YYYYMMDD/HH/atmos/gdas.tHHz.sfcshp.tm00.bufr_d -> cache

runs the task per cycle with the providers that read sfcshp subsets, in a scratch
COMROOT (<work>/com: the task rebuilds ocean/<type>/ directories, so it must not write
into the database), and copies the requested obs spaces into the database:

  <out>/gdas.YYYYMMDD/HH/ocean/insitu/gdas.tHHz.<space>.nc

  python -m nnja2obsforge.obsforge_bufr --start 20230101 --end 20230105 --fetch-only  # internet
  scripts/run_obsforge_bufr.sh 20230101 20230105         # obsForge modules + conversion
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta

import yaml

from . import bucket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(ROOT, 'sorc', 'obsForge')
BASIN = ('/scratch3/NCEPDEV/da/Guillaume.Vernieres/runs/gfs-dev/mom6100_grid/'
         'RECCAP2_region_masks_all_v20221025.nc')
SFCSHP = 'conv/convbufr/sfcshp'
# sfcshp subsets obsForge knows (pyobsforge.task.sfcshp.SfcShp.subset_mapping values)
SFCSHP_TAGS = {'ships', 'shipsu', 'dbuoy', 'mbuoy', 'lcman', 'cstgd', 'shipsb', 'dbuoyb',
               'mbuoyb', 'cmanb', 'shipub'}
# obs spaces copied into the database by default: the ones NNJA has no other source for
INSTALL = ['insitu_temp_surface_drifter', 'insitu_temp_surface_ndbc']
log = logging.getLogger('nnja2obsforge.obsforge_bufr')
log.propagate = False            # wxflow (imported by obsForge) reconfigures the root logger
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter('%(asctime)s nnja2obsforge %(levelname)s: %(message)s'))
log.addHandler(_h)
log.setLevel(logging.INFO)


def sfcshp_key(cycle):
    return (f'{bucket.PREFIX}/{SFCSHP}/{cycle:%Y}/{cycle:%m}/'
            f'gdas.{cycle:%Y%m%d}.t{cycle:%H}z.sfcshp.bufr_d.nr')


def providers(home, names=None):
    """obsForge marine BUFR providers reading sfcshp subsets (optionally by name)."""
    with open(os.path.join(home, 'parm', 'marine_bufr_dump_config.yaml')) as f:
        cfg = yaml.safe_load(f)['marinebufrdump']
    out = [p for p in cfg['providers'] if p.get('dump_tag') in SFCSHP_TAGS]
    if names:
        out = [p for p in out if p['name'] in names
               or any(v['provider_var'] in names for v in p['variables'])]
    return cfg, out


def cycles_of(first, last):
    out, c = [], first
    while c <= last + timedelta(hours=18):
        out.append(c)
        c += timedelta(hours=6)
    return out


def needed(provs, cycles):
    """Dump cycles needed: each cycle plus the providers' back/forward windows."""
    back = max([p.get('window', {}).get('back', 0) for p in provs] + [0])
    fwd = max([p.get('window', {}).get('forward', 0) for p in provs] + [0])
    out = set()
    for c in cycles:
        for i in range(-back, fwd + 1):
            out.add(c + timedelta(hours=6 * i))
    return sorted(out)


def stage(cycle, cache, work):
    """Link the cached dump where the obsForge template looks for it; False if absent."""
    src = os.path.join(cache, sfcshp_key(cycle))
    if not os.path.exists(src):
        return False
    d = os.path.join(work, 'obsproc', f'gdas.{cycle:%Y%m%d}', f'{cycle:%H}', 'atmos')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, f'gdas.t{cycle:%H}z.sfcshp.tm00.bufr_d')
    if os.path.lexists(dst):
        os.remove(dst)
    os.symlink(src, dst)
    return True


def run_cycle(cycle, provs, task_cfg, args):
    """Run obsForge's MarineBufrObsPrep for one cycle; returns {space: path} produced."""
    sys.path[:0] = [os.path.join(args.home, 'ush', 'python'),
                    os.path.join(args.home, 'sorc', 'wxflow', 'src')]
    from wxflow import AttrDict
    from pyobsforge.task.marine_bufr_prepobs import MarineBufrObsPrep

    data = os.path.join(args.work, 'run', f'{cycle:%Y%m%d%H}')
    shutil.rmtree(data, ignore_errors=True)
    os.makedirs(data)
    com = os.path.join(args.work, 'com')
    config = AttrDict({
        'HOMEobsforge': args.home, 'RUN': 'gdas', 'PSLOT': 'nnja', 'COMROOT': com,
        'DATA': data, 'OBSPROC_COMROOT': os.path.join(args.work, 'obsproc'),
        'ocean_basin': args.basin_file, 'assim_freq': 6,
        'PDY': datetime(cycle.year, cycle.month, cycle.day), 'cyc': cycle.hour,
        'BUFR2IODA_CONFIG_TEMP': task_cfg['BUFR2IODA_CONFIG_TEMP'],
        'providers': [dict(p) for p in provs]})
    cwd = os.getcwd()
    os.chdir(data)                       # split_by_subset and the converters write in cwd
    try:
        task = MarineBufrObsPrep(config)
        task.initialize()
        task.execute()
        task.finalize()
    finally:
        os.chdir(cwd)
    ocean = os.path.join(com, 'nnja', f'gdas.{cycle:%Y%m%d}', f'{cycle:%H}', 'ocean')
    out = {}
    for p in provs:
        for v in p['variables']:
            f = os.path.join(ocean, f"gdas.t{cycle:%H}z.{v['provider_var']}.nc")
            if os.path.exists(f):
                out[v['provider_var']] = f
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog='nnja2obsforge.obsforge_bufr', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--start', required=True)
    ap.add_argument('--end')
    ap.add_argument('--providers', nargs='+',
                    help='obsForge provider names or obs spaces (default: every provider '
                         'reading sfcshp subsets)')
    ap.add_argument('--install', nargs='+', default=INSTALL,
                    help=f'obs spaces copied into the database (default {INSTALL}); '
                         "'all' for every one produced")
    ap.add_argument('--root', default=ROOT)
    ap.add_argument('--out')
    ap.add_argument('--cache')
    ap.add_argument('--work', help='staging/run directory (default <root>/work/obsforge_bufr)')
    ap.add_argument('--home', default=HOME, help='obsForge checkout (built)')
    ap.add_argument('--basin-file', default=BASIN)
    ap.add_argument('--fetch-only', action='store_true')
    ap.add_argument('--no-fetch', action='store_true')
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    first = datetime.strptime(args.start, '%Y%m%d')
    last = datetime.strptime(args.end or args.start, '%Y%m%d')
    cache = args.cache or os.path.join(args.root, 'cache')
    out = args.out or os.path.join(args.root, 'obsforge')
    args.work = os.path.abspath(args.work or os.path.join(args.root, 'work', 'obsforge_bufr'))
    task_cfg, provs = providers(args.home, args.providers)
    if not provs:
        ap.error('no obsForge provider reads sfcshp subsets with those names')
    log.info('providers: %s', ', '.join(p['name'] for p in provs))
    cycles = cycles_of(first, last)
    dumps = needed(provs, cycles)

    if not args.no_fetch:
        r = bucket.fetch([sfcshp_key(c) for c in dumps], cache, args.workers)
        log.info('sfcshp dumps: %(ok)d fetched, %(missing)d not in NNJA, %(error)d failed', r)
        if r['error']:
            return 1
    if args.fetch_only:
        return 0

    for c in dumps:
        if not stage(c, cache, args.work):
            log.warning('no NNJA sfcshp dump for %s', f'{c:%Y%m%d%H}')
    nbad = 0
    for c in cycles:
        made = run_cycle(c, provs, task_cfg, args)
        log.info('%s: obsForge wrote %s', f'{c:%Y%m%d%H}', ', '.join(sorted(made)) or 'nothing')
        for space, f in sorted(made.items()):
            if args.install != ['all'] and space not in args.install:
                continue
            dst = os.path.join(out, f'gdas.{c:%Y%m%d}', f'{c:%H}', 'ocean', 'insitu',
                               os.path.basename(f))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(f, dst + '.tmp')
            os.replace(dst + '.tmp', dst)
        missing = [s for s in args.install if s != 'all' and s not in made]
        if missing and os.path.exists(os.path.join(cache, sfcshp_key(c))):
            log.warning('%s: no %s', f'{c:%Y%m%d%H}', ', '.join(missing))
            nbad += 1
    return 1 if nbad else 0


if __name__ == '__main__':
    sys.exit(main())
