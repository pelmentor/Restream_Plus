#!/bin/sh
# Restream_Plus — Docker HEALTHCHECK target.
# Phase 10 §H. Probes /livez ONLY.
#
# Why /livez and NOT /readyz:
#   /readyz returns 503 until the master passphrase is loaded. In paste
#   mode the operator pastes interactively — possibly minutes after
#   container start. If HEALTHCHECK probed /readyz, Docker would mark
#   the container `unhealthy` and any orchestrator with a restart-on-
#   unhealthy policy would loop the container before the human ever
#   gets to paste. /livez is unconditional ("the HTTP server is up") —
#   exactly the right contract for liveness probing.
#
# /readyz is for reverse proxies and the panel's diagnostic surface,
# NOT for Docker HEALTHCHECK.
exec curl -fsS --max-time 2 http://127.0.0.1:8000/livez >/dev/null
