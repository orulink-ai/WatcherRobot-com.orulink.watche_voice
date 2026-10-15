#!/usr/bin/env sh
# Shared Python core owns parameters, defaults and exit codes on every platform.
task_python="${PYTHON:-python3}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 127
exec "$task_python" "$script_dir/acoustic_e2e.py" "$@"
