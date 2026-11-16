#!/bin/bash
# Build the obsForge submodule (sorc/obsForge) on a compute node: its bufr2ioda
# converters turn the NNJA conventional BUFR dumps (conv/convbufr/...) into IODA.
# usage (from the repository): sbatch scripts/build_obsforge.sh [build.sh options, e.g. -f]
#   first: git submodule update --init --recursive
#SBATCH --account=da-cpu
#SBATCH --partition=u1-compute
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=24
#SBATCH --time=02:00:00
#SBATCH --job-name=build-obsforge
#SBATCH --output=build-obsforge-%j.out
set -euo pipefail
cd "${NNJA2OBSFORGE_HOME:-${SLURM_SUBMIT_DIR:-$(dirname "$0")/..}}/sorc/obsForge"
BUILD_JOBS=${BUILD_JOBS:-24} ./build.sh "$@"
