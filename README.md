# nnja2obsforge

Ocean and sea-ice observations from the NOAA-NASA Joint Archive (NNJA, public bucket
[`noaa-reanalyses-pds`](https://noaa-reanalyses-pds.s3.amazonaws.com/index.html#observations/reanalysis/),
prefix `observations/reanalysis`) converted to the file names, layout and IODA content of
obsForge's marine output, so that anything reading `common_obsForge` can read them:

```
<root>/obsforge/gdas.YYYYMMDD/HH/ocean/<dir>/gdas.tHHz.<obsforge name>.nc
```

The NNJA → obsForge name mapping is [`mapping.yaml`](mapping.yaml) (table below).

## Usage

Needs Python 3.9+, numpy, netCDF4, PyYAML. Downloading needs internet: a login node or the
`u1-service` partition (compute nodes have none); `--no-fetch` converts from the cache only.

```bash
# one day, every enabled space (4 cycles; ADT 00z only)
python3 -m nnja2obsforge --start 20230101

# a month of Argo and the GTMBA moorings
python3 -m nnja2obsforge --start 20230101 --end 20230131 --spaces 'insitu_*_argo' 'insitu_*_pirata'

# download only (u1-service), convert later anywhere
python3 -m nnja2obsforge --start 20230101 --end 20230131 --spaces 'sst_*' --fetch-only
python3 -m nnja2obsforge --start 20230101 --end 20230131 --spaces 'sst_*' --no-fetch

# batch on u1-service (submit from the repository)
START=20230101 END=20230131 SPACES="rads_adt_* insitu_*" sbatch --export=ALL scripts/run_nnja2obsforge.sh

python3 -m nnja2obsforge --list          # the mapping as a markdown table
```

Options: `--root` (holds `cache/` and `obsforge/`, default the repository), `--out`,
`--cache`, `--cycles 0` (override the cycle hours), `--run` (file prefix, default `gdas`),
`--basin-file`, `--workers`, `--mapping`. Space names accept globs; a space that is off by
default runs only when named exactly. Existing files are overwritten.

`cache/` mirrors the bucket keys; `obsforge/`, `cache/`, `figures/` and logs are not versioned.

## What a file contains

For each selected space and cycle T:

1. **Files.** The month directories `<source>/YYYY/MM/` around the window are listed (the
   bucket's layout is irregular: 1-hourly, 6-hourly and daily files, `iodav2` and `iodav3`
   copies, superob sub-directories); keys matching the space's `files` regexes are kept, the
   first matching pattern winning per file time (iodav3 before iodav2), and those whose
   `cover` overlaps the window are downloaded and read.
2. **Obs.** Window `[T-3h, T+3h)` by default. Kept: finite position/time/value, value inside
   `valid`, NNJA `PreQC` in `preqc` (written out as PreQC 0). Exact duplicates (time,
   lat, lon[, depth]) are dropped; obs are sorted by time, lat, lon[, depth].
3. **Re-dating** (spaces with `window.half` > 3 h), as obsForge's `Rads2Ioda::reDate`:
   obs of `[T-half, T+half)` before/after `[T-3h, T+3h]` are moved to window begin + 1 s /
   end - 1 s, ObsError grows by `redate_error_ratio` × days moved, `originalDateTime`
   keeps the true time.
   - ADT: gdas 00z only, ±48 h, 1.0 m/day, closed interval (as the original nnja_adt
     converter, whose output this reproduces bit for bit).
   - NSIDC daily ice concentration (stamped 12Z): ±12 h, no inflation, so each cycle gets
     one day of maps.
4. **Content** like the obsForge file of the same kind: `MetaData/{dateTime, latitude,
   longitude, oceanBasin}` + `depth, originalDateTime, rcptdateTime, stationID` for in
   situ, + `cycle, mdt, mission, originalDateTime` for ADT; `ObsValue/ObsError/PreQC/<var>`;
   IODA fill values, zlib 6, one chunk; `dateTime` epoch as obsForge (ADT 1858-11-17, SST
   1981-01-01, others 1970-01-01). `oceanBasin` is the RECCAP2 `open_ocean` region of the
   nearest 1° cell (obsForge `OceanMask`). Not in NNJA, written missing: ADT `cycle`/`mdt`,
   `rcptdateTime`, `stationID` (except GTMBA moorings: WMO id). ObsError missing in NNJA →
   kind default (`err`). `obs_source_files` lists the NNJA files used.

## Mapping

Names follow obsForge (`rads_adt_<sat>`, `insitu_<temp|salt>_<profile|surface>_<platform>`,
`<sst|sss|icec>_<sensor>_<sat>_<level>`). Where obsForge has no counterpart the same pattern
is extended (e.g. `insitu_*_bottle`, `*_cci`, `icefb_*` in a new `icefb/` directory).
To add or rename a space, edit `mapping.yaml`; `--list` regenerates this table.

| obsForge name | dir | variable | NNJA source(s) | notes |
|---|---|---|---|---|
| `rads_adt_tx` | adt | absoluteDynamicTopography | `adt/GLORe/topex_poseidon` | TOPEX/Poseidon |
| `rads_adt_e1` | adt | absoluteDynamicTopography | `adt/GLORe/ers1` |  |
| `rads_adt_e2` | adt | absoluteDynamicTopography | `adt/GLORe/ers2` |  |
| `rads_adt_g1` | adt | absoluteDynamicTopography | `adt/GLORe/GFO` | GFO |
| `rads_adt_j1` | adt | absoluteDynamicTopography | `adt/GLORe/jason1` |  |
| `rads_adt_n1` | adt | absoluteDynamicTopography | `adt/GLORe/n1` | Envisat |
| `rads_adt_j2` | adt | absoluteDynamicTopography | `adt/GLORe/jason2` |  |
| `rads_adt_c2` | adt | absoluteDynamicTopography | `adt/GLORe/cryosat2` |  |
| `rads_adt_sa` | adt | absoluteDynamicTopography | `adt/GLORe/saral` |  |
| `rads_adt_j3` | adt | absoluteDynamicTopography | `adt/GLORe/jason3` |  |
| `rads_adt_3a` | adt | absoluteDynamicTopography | `adt/GLORe/sentinel3a` |  |
| `rads_adt_3b` | adt | absoluteDynamicTopography | `adt/GLORe/sentinel3b` |  |
| `rads_adt_6a` | adt | absoluteDynamicTopography | `adt/GLORe/sentinel6a` |  |
| `rads_adt_sw` | adt | absoluteDynamicTopography | `adt/GLORe/swot` |  |
| `insitu_temp_profile_argo` | insitu | waterTemperature | `insitu/wod/pfl` |  |
| `insitu_salt_profile_argo` | insitu | salinity | `insitu/wod/pfl` |  |
| `insitu_temp_profile_glider` | insitu | waterTemperature | `insitu/wod/gld` |  |
| `insitu_salt_profile_glider` | insitu | salinity | `insitu/wod/gld` |  |
| `insitu_temp_profile_xbtctd` | insitu | waterTemperature | `insitu/wod/ctd`<br>`insitu/wod/xbt` | WOD CTD + XBT |
| `insitu_salt_profile_xbtctd` | insitu | salinity | `insitu/wod/ctd` | WOD CTD |
| `insitu_temp_profile_bathy` | insitu | waterTemperature | `insitu/wod/mbt` | WOD MBT |
| `insitu_temp_profile_bottle` | insitu | waterTemperature | `insitu/wod/osd` | WOD OSD (bottle, low-res CTD) |
| `insitu_salt_profile_bottle` | insitu | salinity | `insitu/wod/osd` | WOD OSD (bottle, low-res CTD) |
| `insitu_temp_profile_marinemammal` | insitu | waterTemperature | `insitu/wod/apb` | WOD APB (animal-borne) |
| `insitu_salt_profile_marinemammal` | insitu | salinity | `insitu/wod/apb` | WOD APB (animal-borne) |
| `insitu_temp_profile_drifter` | insitu | waterTemperature | `insitu/wod/drb` | WOD DRB (drifting buoys) |
| `insitu_salt_profile_drifter` | insitu | salinity | `insitu/wod/drb` | WOD DRB (drifting buoys) |
| `insitu_temp_profile_mooring` | insitu | waterTemperature | `insitu/wod/mrb` | WOD MRB (moored buoys; overlaps GTMBA below) |
| `insitu_salt_profile_mooring` | insitu | salinity | `insitu/wod/mrb` | WOD MRB (moored buoys; overlaps GTMBA below) |
| `insitu_temp_profile_uor` | insitu | waterTemperature | `insitu/wod/uor` | WOD UOR (undulating recorders) |
| `insitu_salt_profile_uor` | insitu | salinity | `insitu/wod/uor` | WOD UOR (undulating recorders) |
| `insitu_temp_profile_wodncei` | insitu | waterTemperature | `insitu/ncei/wod` | (off by default) |
| `insitu_salt_profile_wodncei` | insitu | salinity | `insitu/ncei/wod` | (off by default) |
| `insitu_temp_profile_taotriton` | insitu | waterTemperature | `insitu/moorings/tao/T` |  |
| `insitu_salt_profile_taotriton` | insitu | salinity | `insitu/moorings/tao/S` |  |
| `insitu_temp_surface_taotriton` | insitu | seaSurfaceTemperature | `insitu/moorings/tao/SST` |  |
| `insitu_salt_surface_taotriton` | insitu | seaSurfaceSalinity | `insitu/moorings/tao/SSS` |  |
| `insitu_temp_profile_pirata` | insitu | waterTemperature | `insitu/moorings/pirata/T` |  |
| `insitu_salt_profile_pirata` | insitu | salinity | `insitu/moorings/pirata/S` |  |
| `insitu_temp_surface_pirata` | insitu | seaSurfaceTemperature | `insitu/moorings/pirata/SST` |  |
| `insitu_salt_surface_pirata` | insitu | seaSurfaceSalinity | `insitu/moorings/pirata/SSS` |  |
| `insitu_temp_profile_rama` | insitu | waterTemperature | `insitu/moorings/rama/T` |  |
| `insitu_salt_profile_rama` | insitu | salinity | `insitu/moorings/rama/S` |  |
| `insitu_temp_surface_rama` | insitu | seaSurfaceTemperature | `insitu/moorings/rama/SST` |  |
| `insitu_salt_surface_rama` | insitu | seaSurfaceSalinity | `insitu/moorings/rama/SSS` |  |
| `sst_avhrr_n07_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa07` | NESDIS AVHRR GAC, 0.25 deg superobs |
| `sst_avhrr_n09_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa09` | NESDIS AVHRR GAC, 0.25 deg superobs |
| `sst_avhrr_n11_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa11` | NESDIS AVHRR GAC, 0.25 deg superobs |
| `sst_avhrr_n12_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa12` | NESDIS AVHRR GAC, 0.25 deg superobs |
| `sst_avhrr_n14_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa14` | NESDIS AVHRR GAC, 0.25 deg superobs |
| `sst_avhrr_n15_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa15` | NESDIS AVHRR GAC, 0.25 deg superobs |
| `sst_avhrr_n16_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa16` | NESDIS AVHRR GAC, 0.25 deg superobs |
| `sst_avhrr_n19_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/noaa19` | NESDIS ACSPO |
| `sst_avhrrf_ma_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/metopa` | NESDIS ACSPO Metop-A |
| `sst_amsr2_gw1_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/amsr2` | NESDIS AMSR2 (GCOM-W1) |
| `sst_gmi_gpm_l3u` | sst | seaSurfaceTemperature | `sst/nesdis/gpm` | NESDIS GMI (GPM) |
| `sst_windsat_cor_l3u` | sst | seaSurfaceTemperature | `sst/jpl/coriolis` | JPL WindSat (Coriolis) |
| `sst_avhrr_n07_l3u_cci` | sst | seaSurfaceTemperature | `sst/esacci/noaa07` | ESA CCI |
| `sst_avhrr_n09_l3u_cci` | sst | seaSurfaceTemperature | `sst/esacci/noaa09` | ESA CCI |
| `sst_avhrr_n11_l3u_cci` | sst | seaSurfaceTemperature | `sst/esacci/noaa11` | ESA CCI |
| `sst_avhrr_n14_l3u_cci` | sst | seaSurfaceTemperature | `sst/esacci/noaa14` | ESA CCI |
| `sst_avhrr_multi_l3c_cci` | sst | seaSurfaceTemperature | `sst/esacci/multi` | ESA CCI, all AVHRRs |
| `sst_avhrr_cci20cm_l3` | sst | seaSurfaceTemperature | `sst/nggodas/avhrr` | ngGODAS ESA CCI 20 cm SST, 6-hourly (its ombg/oman are dropped) |
| `sss_smap_l2` | sss | seaSurfaceSalinity | `sss/jpl/sentinel1a` | JPL SMAP L2 (NNJA directory says sentinel1a) |
| `icec_nsidc_north` | icec | seaIceFraction | `icec/GLORe/nh` | NSIDC daily, re-dated into all 4 cycles (PreQC is 1 everywhere) |
| `icec_nsidc_south` | icec | seaIceFraction | `icec/GLORe/sh` | NSIDC daily, re-dated into all 4 cycles (PreQC is 1 everywhere) |
| `icec_ssmi_dmsp_l2` | icec | seaIceFraction | `icec/emc/dmsp` | EMC retrieval from SSM/I (DMSP); PreQC 1 kept |
| `icec_ssmis_dmsp_l2` | icec | seaIceFraction | `icec/emc/dmsp` | EMC retrieval from SSMIS (DMSP); PreQC 1 kept |
| `icefb_siral_c2_l2` | icefb | seaIceFreeboard | `icefb/esa/cryosat2` | ESA CryoSat-2 L2 freeboard; PreQC 0x2000 kept |

Not mapped: `adt/nesdis/*` (to 2020, same missions as GLORe), `icec/nsidc/{nh,sh}` (iodav2
copy of `icec/GLORe`), mooring currents (`insitu/moorings/*/CUR`, no obsForge variable).

### Notes on the sources

- In situ WOD files already carry NNJA's QC (`PreQC=99` = far from WOA23); only PreQC 0
  is kept. Their ObsError is a constant 0.5. TAO/TRITON T and SST stop in 2021 in the
  bucket; PIRATA/RAMA run to 2024. GTMBA PreQC: PMEL quality 1-3 → 0.
- `insitu/ncei/wod` mixes every WOD instrument type in daily T+S files with no platform
  field, so it duplicates `insitu/wod`; its spaces (`*_wodncei`) are off by default.
- NESDIS AVHRR GAC SST for NOAA-7…16 is 0.25° hourly superobs; NOAA-19 and Metop-A are
  daily ACSPO L3U. `sst/esacci/multi` carries the same satellite as `noaa14` in 1998.
- `sss/jpl/sentinel1a` is SMAP (the NNJA directory name is wrong).
- PreQC rules inferred from the data (not documented by NNJA): EMC SSM/I and SSMIS sea
  ice keep PreQC 1 (3-5 are masked points with concentration 0); CryoSat-2 freeboard
  keeps PreQC 0x2000 (other values carry 0 m, negative or no freeboard). NSIDC sea ice has
  PreQC 1 everywhere and keeps all.
- The `valid` ranges in `mapping.yaml` are coarse gross checks, not a QC.

## Checks

```bash
python3 scripts/check_schema.py obsforge/gdas.20230101   # vs common_obsForge files of each kind
python3 scripts/plot_adt.py 2023010100                   # ADT maps vs obsForge (cartopy env)
```

Verified (2026-11): ADT 2023-01-01/02 identical to the original `nnja_adt2obsforge.py`
output; one day of every kind (2023-01-01 in situ + NSIDC ice, 2015-04-01 SSS/AVHRR/EMC
ice/freeboard, 2019-01-01 AMSR2/GMI/WindSat, 1998-01-01 ESA CCI/NESDIS GAC/TOPEX/ERS-2/
NCEI WOD) passes `check_schema.py` (variables, types, fills, epochs, dateTime in window)
and init-mom6 reads the in situ files.

## Layout

```
mapping.yaml                 NNJA -> obsForge mapping (kinds, spaces, sources)
nnja2obsforge/__main__.py    command line
nnja2obsforge/bucket.py      S3 listing, file discovery and time stamps, downloads
nnja2obsforge/ioda.py        NNJA readers (IODA v2/v3, PMEL), obsForge writer, ocean basins
nnja2obsforge/convert.py     windows, selection, de-duplication, re-dating
nnja2obsforge/mapping.py     mapping.yaml loader
scripts/                     slurm wrapper, schema check, ADT plots
```
