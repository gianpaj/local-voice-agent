#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p vendor
if [[ ! -d vendor/eqbench3/.git ]]; then
  git clone --filter=blob:none --no-checkout https://github.com/EQ-bench/eqbench3.git vendor/eqbench3
  git -C vendor/eqbench3 checkout --detach affc5a000bbdb965b807e09ca7875f8530fa8f56
fi
if [[ ! -d vendor/tau2-bench/.git ]]; then
  git clone --filter=blob:none --no-checkout https://github.com/sierra-research/tau2-bench.git vendor/tau2-bench
  git -C vendor/tau2-bench checkout --detach 033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f
fi
test "$(git -C vendor/eqbench3 rev-parse HEAD)" = affc5a000bbdb965b807e09ca7875f8530fa8f56
test "$(git -C vendor/tau2-bench rev-parse HEAD)" = 033bf082f48e4a3c6a1256208e93cf2ce9c3bb6f
uv venv --python 3.12 .venv
# Allow the security-patched LiteLLM release in the pinned benchmark metadata.
.venv/bin/python - <<'PY'
from pathlib import Path

path = Path('vendor/tau2-bench/pyproject.toml')
text = path.read_text()
original = '"litellm>=1.80.15,<1.82.7"'
patched = '"litellm>=1.84.0,<1.85"'
if text.count(original) + text.count(patched) != 1:
    raise SystemExit('Unexpected tau2 LiteLLM requirement')
path.write_text(text.replace(original, patched))
PY
uv pip sync --python .venv/bin/python requirements.lock
uv pip check --python .venv/bin/python
.venv/bin/python prepare_pilot.py
