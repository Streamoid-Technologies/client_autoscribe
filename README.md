# Client Autoscribe v2 Queuing Design

## Python Environment

This project is aligned to the platform Python 3.13 baseline and uses `uv`
for dependency management. The source of truth is `pyproject.toml` plus
`uv.lock`; `requirements.txt` is kept only as a compatibility reference.

Setup:

```sh
uv sync --frozen
```

Verification:

```sh
uv lock --check
uv sync --frozen
uv run python -m compileall -q .
PYTHONPATH="$(pwd)/.." uv run python - <<'PY'
import importlib

modules = [
    "client_autoscribe_api_v2",
    "client_autoscribe_worker_v2",
    "autoscribe_listener",
    "tracking_dashboard_api",
    "rpa_interface",
]

for module in modules:
    importlib.import_module(module)
PY
```

Common service entry points:

```sh
uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
uv run celery -A client_autoscribe_worker_v2 worker -Q client_autoscribe_v2
uv run gunicorn -c gunicorn.conf.py rpa_interface:app
```

## Tasks
As per client_autoscribe_worker_v2 module:

### Active
- post_to_client: (highest priority)
  - better to split by load / priority vendors
  - client_autoscribe_v2_abfrl, client_autoscribe_v2_streamoid, client_autoscribe_v2_misc
- translate_and_save: (medium priority)
  - expected to be fast; can be managed with single queue
  - client_autoscribe_v2
- trigger_precompute: (lowest priority)
  - on new products; can be slow in case of large number of products
  - client_autoscribe_v2_misc
   
### Deprecated
- queue_post_to_teams
- post_rpa_files
- fetch_files_and_store
- update_rpa_status

## Queues
- client_autoscribe_v2
- client_autoscribe_v2_misc
- client_autoscribe_v2_abfrl
- client_autoscribe_v2_streamoid
