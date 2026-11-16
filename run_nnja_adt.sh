#!/bin/bash
# NNJA ADT -> obsForge-format files for the gdas 00z cycles START..END (u1-service: internet).
# usage: [START=20230101] [END=20230131] sbatch run_nnja_adt.sh [extra nnja_adt2obsforge.py options]
#SBATCH --account=da-cpu
#SBATCH --partition=u1-service
#SBATCH --ntasks=1
#SBATCH --time=01:00:00
#SBATCH --job-name=nnja-adt
#SBATCH --output=nnja-adt-%j.out
set -euo pipefail
cd /scratch3/NCEPDEV/da/Guillaume.Vernieres/data/nnja_adt
python3 nnja_adt2obsforge.py --start ${START:-20230101} --end ${END:-20230131} "$@"
