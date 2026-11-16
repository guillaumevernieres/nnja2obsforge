#!/bin/bash
# Convert the NNJA sfcshp BUFR dumps of START..END with obsForge (sorc/obsForge, built by
# scripts/build_obsforge.sh): drifters (insitu_temp_surface_drifter), moored buoys
# (insitu_temp_surface_ndbc), ... See nnja2obsforge/obsforge_bufr.py.
# usage: [sbatch --export=ALL] scripts/run_obsforge_bufr.sh START END [options]
#   dumps must be cached first (internet):
#   python3 -m nnja2obsforge.obsforge_bufr --start START --end END --fetch-only
#SBATCH --account=da-cpu
#SBATCH --partition=u1-service
#SBATCH --ntasks=1
#SBATCH --time=02:00:00
#SBATCH --job-name=nnja-obsforge-bufr
#SBATCH --output=nnja-obsforge-bufr-%j.out
set -eu
REPO=$(cd "${NNJA2OBSFORGE_HOME:-${SLURM_SUBMIT_DIR:-$(dirname "$0")/..}}" && pwd)
START=$1; END=$2; shift 2
export HOMEobsforge=${REPO}/sorc/obsForge
set_trace() { :; }
set +u
source "${HOMEobsforge}/ush/load_obsforge_modules.sh" > /dev/null
set -u
export PATH="${HOMEobsforge}/build/bin:${PATH}"
cd "${REPO}"
python -m nnja2obsforge.obsforge_bufr --start "${START}" --end "${END}" --no-fetch "$@"
