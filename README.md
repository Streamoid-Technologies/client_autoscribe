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

## Production Operations Quick Reference

Production currently runs from:

```sh
/home/ubuntu/git/experiments/client_autoscribe
```

The production machine is `ubuntu@65.21.91.53`. Nginx exposes
`/api/autoscribe` and forwards it to uWSGI on `127.0.0.1:4008`.

Active API process:

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
~/.local/bin/uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
```

Production cron entries:

```cron
@reboot cd ~/git/experiments/client_autoscribe && ~/.local/bin/uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
*/30 * * * * cd ~/git/experiments/client_autoscribe && ~/.local/bin/uv run python client_autoscribe_to_catalogix_cron.py
0 0 */2 * *  python /home/ubuntu/custom-rules/custom_rules_input_del_cron.py
```

User systemd Celery units:

```sh
systemctl --user status celery@client_autoscribe_v2.service
systemctl --user status celery@client_autoscribe_v2_abfrl.service
systemctl --user status celery@client_autoscribe_v2_streamoid.service
systemctl --user status celery@client_autoscribe_v2_misc.service
```

The unit template is `~/.config/systemd/user/celery@.service`. Runtime env
files are in `~/bin/client_autoscribe_v2*.conf` and should point to:

```sh
WORKING_DIR="/home/ubuntu/git/experiments/client_autoscribe"
CELERY_BIN="/home/ubuntu/git/experiments/client_autoscribe/.venv/bin/celery"
```

Health checks:

```sh
curl -ksS -o /tmp/autoscribe_dashboard.html -w 'status=%{http_code}\n' \
  https://cataloging.streamoid.com/api/autoscribe/vendors/abfrl_lbrd_prod/catalogix-dashboard

ss -ltnp '( sport = :4008 )'

cd /home/ubuntu/git/experiments/client_autoscribe
.venv/bin/celery -A client_autoscribe_worker_v2 status --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect active_queues --timeout=10
sudo rabbitmqctl list_queues -p calm_host name messages messages_ready messages_unacknowledged consumers
```

Important production note: the current `.venv` is healthy and package-isolated,
but its base interpreter is `/home/ubuntu/miniconda3/bin/python3.13`.
It is not using the old conda environment
`/home/ubuntu/miniconda3/envs/client_autoscribe`. If Miniconda is ever moved or
removed, recreate `.venv` with a uv-managed Python 3.13 during a maintenance
window.

When moving or renaming the repository path, recreate `.venv` at the final path:

```sh
rm -rf .venv
~/.local/bin/uv sync --frozen
```

The previous production repository from the uv cutover is preserved as
`/home/ubuntu/git_old_20260630_184627`. Cutover backups are preserved in
`/home/ubuntu/cutover_backup_20260630_184446`.

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
