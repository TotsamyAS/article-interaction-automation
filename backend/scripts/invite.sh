#!/bin/sh
# Invoke only in Docker. The user receives the link; do not capture it in logs.
set -eu
cd /app
exec python -m app.invitations "$@"
