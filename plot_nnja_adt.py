#!/usr/bin/env python3
"""Scatter maps of the NNJA ADT files of one gdas cycle, with obsForge for comparison.

usage: plot_nnja_adt.py YYYYMMDDHH [--root DIR] [--obsforge DIR] [--out DIR]
Needs matplotlib + cartopy, e.g. /scratch3/NCEPDEV/da/Guillaume.Vernieres/venvs/gdas-marine-viz.

Figures (in --out, default <root>/figures/YYYYMMDDHH):
  window.png   obs of the 6 h window (originalDateTime), NNJA next to obsForge, colour = ADT
  all.png      every obs of the file (cycle +/- 48 h), colour = ADT and ObsError
  by_sat.png   every obs, one panel per satellite, colour = ADT
  basin.png    every obs, colour = oceanBasin
"""

import argparse
import glob
import os
from datetime import datetime, timedelta

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import netCDF4 as nc  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
OBSFORGE = '/scratch3/NCEPDEV/da/common_obsForge'
VAR = 'absoluteDynamicTopography'
EPOCH = datetime(1858, 11, 17)
BASINS = ['land/none', 'Atlantic', 'Pacific', 'Indian', 'Arctic', 'Southern']


def load(files):
    """Concatenated fields of the files; obsForge fill records (ObsError >= 1e3) dropped."""
    out = {k: [] for k in ('lat', 'lon', 't', 't0', 'y', 'err', 'basin', 'sat')}
    for f in files:
        with nc.Dataset(f) as ds:
            e = np.ma.filled(ds[f'ObsError/{VAR}'][:], np.nan)
            ok = np.isfinite(e) & (e < 1.0e3)
            out['lat'].append(ds['MetaData/latitude'][:][ok])
            out['lon'].append(ds['MetaData/longitude'][:][ok])
            out['t'].append(ds['MetaData/dateTime'][:][ok].astype(np.int64))
            out['t0'].append(ds['MetaData/originalDateTime'][:][ok].astype(np.int64))
            out['y'].append(ds[f'ObsValue/{VAR}'][:][ok])
            out['err'].append(e[ok])
            out['basin'].append(ds['MetaData/oceanBasin'][:][ok])
            code = os.path.basename(f).split('_')[-1][:-3]
            out['sat'].append(np.full(ok.sum(), code))
    return {k: np.concatenate(v) if v else np.array([]) for k, v in out.items()}


def axes(fig, n, ncols):
    nrows = (n + ncols - 1) // ncols
    axs = [fig.add_subplot(nrows, ncols, i + 1, projection=ccrs.Robinson())
           for i in range(n)]
    for ax in axs:
        ax.set_global()
        ax.add_feature(cfeature.LAND, facecolor='0.8', zorder=2)
        ax.coastlines(linewidth=0.4, zorder=3)
    return axs


def scatter(ax, d, m, c, title, **kw):
    sc = ax.scatter(d['lon'][m], d['lat'][m], c=c[m], s=0.3, linewidths=0,
                    transform=ccrs.PlateCarree(), rasterized=True, zorder=1, **kw)
    ax.set_title(f'{title} (n={int(m.sum())})', fontsize=9)
    return sc


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cdate', help='YYYYMMDDHH')
    ap.add_argument('--root', default=os.path.join(ROOT, 'obsforge'))
    ap.add_argument('--obsforge', default=OBSFORGE)
    ap.add_argument('--out')
    args = ap.parse_args()
    cyc = datetime.strptime(args.cdate, '%Y%m%d%H')
    sub = f'gdas.{cyc:%Y%m%d}/{cyc:%H}/ocean/adt'
    out = args.out or os.path.join(ROOT, 'figures', args.cdate)
    os.makedirs(out, exist_ok=True)

    new = load(sorted(glob.glob(f'{args.root}/{sub}/*.nc')))
    ref = load(sorted(glob.glob(f'{args.obsforge}/{sub}/*.nc')))
    wb, we = [int((cyc + timedelta(hours=h) - EPOCH).total_seconds()) for h in (-3, 3)]
    adt = dict(cmap='RdYlBu_r', vmin=-1.5, vmax=1.5)
    stamp = f'{cyc:%Y-%m-%d %Hz}'

    # 6 h window: NNJA vs obsForge
    fig = plt.figure(figsize=(14, 4.6))
    a1, a2 = axes(fig, 2, 2)
    inw = lambda d: (d['t0'] >= wb) & (d['t0'] <= we)  # noqa: E731
    scatter(a1, new, inw(new), new['y'], f'NNJA GLORe, obs in the 6 h window', **adt)
    sc = scatter(a2, ref, inw(ref), ref['y'], f'obsForge RADS, obs in the 6 h window', **adt)
    fig.colorbar(sc, ax=[a1, a2], shrink=0.7, label='ADT [m]')
    fig.suptitle(f'ADT, gdas {stamp}, all satellites')
    fig.savefig(f'{out}/window.png', dpi=150)
    plt.close(fig)

    # every obs of the files: ADT and ObsError
    fig = plt.figure(figsize=(14, 4.6))
    a1, a2 = axes(fig, 2, 2)
    allm = np.ones(new['y'].size, bool)
    o = np.argsort(-new['err'])            # smallest errors (in window) drawn last
    newo = {k: v[o] for k, v in new.items()}
    sc = scatter(a1, newo, allm, newo['y'], 'ADT, cycle +/- 48 h', **adt)
    fig.colorbar(sc, ax=a1, shrink=0.7, label='ADT [m]')
    sc = scatter(a2, newo, allm, newo['err'], 'ObsError after re-dating', cmap='viridis_r',
                 vmin=0.1, vmax=2.0)
    fig.colorbar(sc, ax=a2, shrink=0.7, label='ObsError [m]')
    fig.suptitle(f'NNJA ADT, gdas {stamp}, all satellites')
    fig.savefig(f'{out}/all.png', dpi=150)
    plt.close(fig)

    # one panel per satellite
    sats = sorted(set(new['sat']))
    fig = plt.figure(figsize=(14, 2.4 * ((len(sats) + 1) // 2) + 1))
    for ax, s in zip(axes(fig, len(sats), 2), sats):
        sc = scatter(ax, newo, newo['sat'] == s, newo['y'], f'rads_adt_{s}', **adt)
    fig.colorbar(sc, ax=fig.axes, shrink=0.6, label='ADT [m]')
    fig.suptitle(f'NNJA ADT by satellite, gdas {stamp}, cycle +/- 48 h')
    fig.savefig(f'{out}/by_sat.png', dpi=150)
    plt.close(fig)

    # ocean basins
    fig = plt.figure(figsize=(8, 4.6))
    (ax,) = axes(fig, 1, 1)
    cmap = ListedColormap(['k', 'tab:blue', 'tab:orange', 'tab:green', 'tab:purple', 'tab:red'])
    sc = scatter(ax, new, allm, new['basin'], 'MetaData/oceanBasin', cmap=cmap,
                 norm=BoundaryNorm(np.arange(-0.5, 6), 6))
    cb = fig.colorbar(sc, ax=ax, shrink=0.7, ticks=range(6))
    cb.ax.set_yticklabels([f'{i} {b}' for i, b in enumerate(BASINS)])
    fig.suptitle(f'NNJA ADT ocean basins, gdas {stamp}')
    fig.savefig(f'{out}/basin.png', dpi=150)
    plt.close(fig)
    print('figures in', out)


if __name__ == '__main__':
    main()
