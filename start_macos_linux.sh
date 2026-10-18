#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
export PROJ_NETWORK=OFF
export PYTHONUTF8=1
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python launch_gui.py
fi
printf '%s
' '请先按 README.md 准备 .venv；本脚本不会自动安装依赖。'
exit 1
