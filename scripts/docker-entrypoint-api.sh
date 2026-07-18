#!/bin/sh
set -eu

mkdir -p "${PDF_EDITOR_DATA}"
chown -R app:app "${PDF_EDITOR_DATA}"

exec gosu app "$@"
