#!/bin/sh
# Restream_Plus — container entrypoint.
# Phase 10 §K — minimal. Runs as PID 1 briefly; execs to /init so the
# real PID 1 is s6-svscan (proper zombie reaping + signal forwarding).
#
# Constraints:
#   - No `set -x` (would echo RESTREAM_MASTER_PASSPHRASE on `docker logs`).
#   - No mkdir/chown of /data — that's the `init-data` s6 oneshot (§F).
#   - Exactly one fail-fast gate (env-mode passphrase). Pydantic
#     validates the same condition; the entrypoint guard gives a
#     friendlier first line of stderr.
set -eu
umask 027

if [ "${RESTREAM_PASSPHRASE_SOURCE:-env}" = "env" ] \
   && [ -z "${RESTREAM_MASTER_PASSPHRASE:-}" ]; then
    echo "ERROR: RESTREAM_PASSPHRASE_SOURCE=env (default) but RESTREAM_MASTER_PASSPHRASE is unset." >&2
    echo "       Either set the passphrase, or use RESTREAM_PASSPHRASE_SOURCE=paste." >&2
    exit 1
fi

exec /init
