#!/bin/bash
# NNJA -> obsForge-format files for the cycles of START..END, on u1-service (internet).
# usage: [START=20230101] [END=20230131] [SPACES="insitu_* rads_adt_*"] \
#        sbatch --export=ALL scripts/run_nnja2obsforge.sh [extra nnja2obsforge options]
#SBATCH --account=da-cpu
#SBATCH --partition=u1-service
#SBATCH --ntasks=1
#SBATCH --time=04:00:00
#SBATCH --job-name=nnja2obsforge
#SBATCH --output=nnja2obsforge-%j.out
set -euo pipefail
cd "${NNJA2OBSFORGE_HOME:-${SLURM_SUBMIT_DIR:-$(dirname "$0")/..}}"
set -f   # keep the space globs for nnja2obsforge
python3 -m nnja2obsforge --start "${START:-20230101}" --end "${END:-${START:-20230131}}" \
    ${SPACES:+--spaces ${SPACES}} "$@"
