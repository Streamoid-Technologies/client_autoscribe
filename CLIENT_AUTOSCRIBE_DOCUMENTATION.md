# Client Autoscribe System Documentation

## Overview

The Client Autoscribe system is a comprehensive product data management platform that handles the complete lifecycle of product data from client uploads through curation, review, and final delivery back to vendors. The system integrates with Catalogix for data curation and provides a dashboard for internal team management.

Repository: [Streamoid-Technologies/client_autoscribe](https://github.com/Streamoid-Technologies/client_autoscribe/tree/main)

## System Architecture

The system follows a workflow where product data flows through several stages:
1. **Products POST** → **Products SAVE client-autoscribe** → **Post to Catalogix** → **Curate in Catalogix** → **Import back to client-autoscribe** → **Review** → **Post To Client**

## Python Environment

The project is aligned to the platform Python 3.13 baseline and uses `uv` for
dependency management. Use `pyproject.toml` and `uv.lock` as the source of
truth; `requirements.txt` is retained only as a compatibility reference.

Install dependencies with:

```bash
uv sync --frozen
```

Run services through the locked environment, for example:

```bash
uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
uv run celery -A client_autoscribe_worker_v2 worker -Q client_autoscribe_v2
uv run gunicorn -c gunicorn.conf.py rpa_interface:app
```


Prod Machine: ssh ubuntu@65.21.91.53 (Hetzner)

Start: cd /home/ubuntu/git/experiments/client_autoscribe/ && ~/.local/bin/uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini

Restart: ~/.local/bin/uv run uwsgi --reload /home/ubuntu/logs/client_autoscribe_v2.pid

Local: ~/.local/bin/uv run uwsgi --http-socket :4009 --wsgi-file client_autoscribe_api_v2.py --callable application

## Production Runtime Snapshot

This section captures the current production deployment state after the uv
cutover. Keep it current whenever paths, process managers, ports, queues, or
runtime ownership change.

### Machine and Repository

- Production host: `ubuntu@65.21.91.53`
- Live repository path: `/home/ubuntu/git/experiments/client_autoscribe`
- Live branch: `main`
- Git remote: `Streamoid-Technologies/client_autoscribe`
- Previous production repository preserved at:
  `/home/ubuntu/git_old_20260630_184627`
- Cutover backup artifacts preserved at:
  `/home/ubuntu/cutover_backup_20260630_184446`

The old repository path should not be used by production after cutover. Use it
only as a rollback/reference artifact.

### HTTP and uWSGI

Nginx routes autoscribe traffic to uWSGI:

```nginx
location /api/autoscribe {
    include uwsgi_params;
    uwsgi_pass localhost:4008;
    uwsgi_read_timeout 300s;
    add_header 'Access-Control-Allow-Origin' '*' always;
}
```

uWSGI runs from the project uv environment:

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
~/.local/bin/uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
```

Expected runtime facts:

```sh
ss -ltnp '( sport = :4008 )'
cat /home/ubuntu/logs/client_autoscribe_v2.pid
readlink -f /proc/$(cat /home/ubuntu/logs/client_autoscribe_v2.pid)/exe
readlink -f /proc/$(cat /home/ubuntu/logs/client_autoscribe_v2.pid)/cwd
```

Expected uWSGI executable/cwd:

```text
/home/ubuntu/git/experiments/client_autoscribe/.venv/bin/uwsgi
/home/ubuntu/git/experiments/client_autoscribe
```

### Public Endpoints

Safe read-only endpoint checks:

```sh
curl -ksS -o /tmp/autoscribe_dashboard.html -w 'status=%{http_code} bytes=%{size_download} time=%{time_total}\n' \
  https://cataloging.streamoid.com/api/autoscribe/vendors/abfrl_lbrd_prod/catalogix-dashboard

curl -ksS -L -o /tmp/abfrl_prod.html -w 'status=%{http_code} bytes=%{size_download}\n' \
  https://cataloging.streamoid.com/v1/api/abfrl/prod/

curl -ksS -L -o /tmp/grupo_soma_prod.html -w 'status=%{http_code} bytes=%{size_download}\n' \
  https://cataloging.streamoid.com/v1/api/grupo_soma/prod/
```

The dashboard response should contain `Client Autoscribe Dashboard`.

Avoid using mutation endpoints for health checks, including endpoints that
post, import, export, mark reviewed, trigger RPA, or post to client.

### Cron

Production cron for this service should be:

```cron
@reboot cd ~/git/experiments/client_autoscribe && ~/.local/bin/uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
*/30 * * * * cd ~/git/experiments/client_autoscribe && ~/.local/bin/uv run python client_autoscribe_to_catalogix_cron.py
0 0 */2 * *  python /home/ubuntu/custom-rules/custom_rules_input_del_cron.py
```

Verify with:

```sh
crontab -l
systemctl is-active cron
```

There should be no active cron entries using:

```text
/home/ubuntu/git_old_*
/home/ubuntu/git2/experiments/client_autoscribe
/home/ubuntu/miniconda3/envs/client_autoscribe
uwsgi_python3
python3 client_autoscribe_to_catalogix_cron.py
```

### Celery and RabbitMQ

Client Autoscribe workers run as user systemd services:

```sh
celery@client_autoscribe_v2.service
celery@client_autoscribe_v2_abfrl.service
celery@client_autoscribe_v2_streamoid.service
celery@client_autoscribe_v2_misc.service
```

The unit template is:

```sh
/home/ubuntu/.config/systemd/user/celery@.service
```

Runtime env files are:

```sh
/home/ubuntu/bin/client_autoscribe_v2.conf
/home/ubuntu/bin/client_autoscribe_v2_abfrl.conf
/home/ubuntu/bin/client_autoscribe_v2_streamoid.conf
/home/ubuntu/bin/client_autoscribe_v2_misc.conf
```

Each env file should use absolute paths similar to:

```sh
WORKING_DIR="/home/ubuntu/git/experiments/client_autoscribe"
CELERY_BIN="/home/ubuntu/git/experiments/client_autoscribe/.venv/bin/celery"
CELERY_APP="client_autoscribe_worker_v2"
```

Do not rely on nested variable expansion inside systemd `EnvironmentFile`
values. During the uv cutover, `${WORKING_DIR}/.venv/bin/celery` failed under
systemd with `status=127`; absolute `CELERY_BIN` paths fixed it.

RabbitMQ vhost:

```text
calm_host
```

Expected autoscribe queues:

```text
client_autoscribe_v2
client_autoscribe_v2_abfrl
client_autoscribe_v2_streamoid
client_autoscribe_v2_misc
```

Celery/RabbitMQ verification:

```sh
systemctl --user is-active \
  celery@client_autoscribe_v2.service \
  celery@client_autoscribe_v2_abfrl.service \
  celery@client_autoscribe_v2_streamoid.service \
  celery@client_autoscribe_v2_misc.service

cd /home/ubuntu/git/experiments/client_autoscribe
.venv/bin/celery -A client_autoscribe_worker_v2 status --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect ping --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect active_queues --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect active --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect reserved --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect scheduled --timeout=10

sudo rabbitmqctl list_queues -p calm_host name messages messages_ready messages_unacknowledged consumers
```

Healthy state means all four autoscribe workers respond, expected queues have
consumers, and there is no unexpected ready/unacknowledged backlog.

### Supporting Services

These should be active for normal production operation:

```sh
systemctl is-active cron
systemctl is-active nginx
systemctl is-active rabbitmq-server
systemctl is-active redis-server
systemctl is-active mongod
```

### Logs

API/uWSGI:

```sh
tail -f /home/ubuntu/logs/uwsgi/client_autoscribe_v2_log.txt
```

Celery:

```sh
tail -f /home/ubuntu/logs/client_autoscribe_v2/client_autoscribe_v2.log
tail -f /home/ubuntu/logs/client_autoscribe_v2_abfrl/client_autoscribe_v2_abfrl.log
tail -f /home/ubuntu/logs/client_autoscribe_v2_streamoid/client_autoscribe_v2_streamoid.log
tail -f /home/ubuntu/logs/client_autoscribe_v2_misc/client_autoscribe_v2_misc.log
```

Post-start log checks:

```sh
rg '2026-06-30 (18:5[0-9]|19:).*ready|Connected to amqp' /home/ubuntu/logs/client_autoscribe_v2*/*.log
rg 'ERROR|CRITICAL|Traceback|Exception|FAILED|Permission denied|No such file|ModuleNotFoundError' \
  /home/ubuntu/logs/client_autoscribe_v2*/*.log
```

Old shutdown warnings from the pre-cutover Python 3.10 conda workers may still
exist in historical log files. Treat new post-start errors differently from old
shutdown noise.

### uv and Python Runtime Notes

The current `.venv` is healthy and package-isolated:

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
~/.local/bin/uv lock --check
~/.local/bin/uv sync --frozen --dry-run
~/.local/bin/uv run python - <<'PY'
import os, sys
print("executable=" + sys.executable)
print("real_executable=" + os.path.realpath(sys.executable))
print("prefix=" + sys.prefix)
print("base_prefix=" + sys.base_prefix)
PY
```

Known production caveat:

- `.venv/bin/python` resolves to `/home/ubuntu/miniconda3/bin/python3.13`.
- `sys.prefix` is the project `.venv`.
- `sys.base_prefix` is `/home/ubuntu/miniconda3`.
- Packages load from `.venv/lib/python3.13/site-packages`.
- Production does not use the old conda env
  `/home/ubuntu/miniconda3/envs/client_autoscribe`.

This is expected Python `venv` behavior: a venv is built on top of a base
interpreter. However, moving/removing `/home/ubuntu/miniconda3` would break the
current venv. If Miniconda needs to be removed, schedule a maintenance window
and recreate `.venv` with uv-managed Python 3.13.

Any time the repository path changes, recreate `.venv` at the final path:

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
rm -rf .venv
~/.local/bin/uv sync --frozen
```

## Production Cutover and Migration Runbook

Use this runbook for future migrations between repository directories, Python
environment rebuilds, or similar production cutovers.

### 1. Preflight Checks

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
git status --short --branch
git log -1 --oneline
~/.local/bin/uv lock --check
~/.local/bin/uv sync --frozen --dry-run
~/.local/bin/uv run python -m compileall -q .
```

Import smoke test:

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
~/.local/bin/uv run python - <<'PY'
for module in [
    "client_autoscribe_api_v2",
    "client_autoscribe_worker_v2",
    "client_autoscribe_to_catalogix_cron",
    "autoscribe_listener",
    "tracking_dashboard_api",
    "rpa_interface",
]:
    __import__(module)
    print(module, "ok")
PY
```

Current production health before touching anything:

```sh
curl -ksS -o /tmp/autoscribe_dashboard.html -w 'status=%{http_code}\n' \
  https://cataloging.streamoid.com/api/autoscribe/vendors/abfrl_lbrd_prod/catalogix-dashboard

systemctl --user is-active \
  celery@client_autoscribe_v2.service \
  celery@client_autoscribe_v2_abfrl.service \
  celery@client_autoscribe_v2_streamoid.service \
  celery@client_autoscribe_v2_misc.service

ss -ltnp '( sport = :4008 )'
crontab -l
```

Stop if the baseline is already unhealthy or if unknown services are using the
same repo, port, queues, or cron entries.

### 2. Backup Runtime Configuration

Use a timestamped backup directory:

```sh
backup_dir="/home/ubuntu/cutover_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$backup_dir"
crontab -l > "$backup_dir/crontab.before"
cp /home/ubuntu/.config/systemd/user/celery@.service "$backup_dir/celery@.service.before"
for f in \
  client_autoscribe_v2.conf \
  client_autoscribe_v2_abfrl.conf \
  client_autoscribe_v2_streamoid.conf \
  client_autoscribe_v2_misc.conf
do
  cp "/home/ubuntu/bin/$f" "$backup_dir/$f.before"
done
cp /home/ubuntu/bin/celery_conda.sh "$backup_dir/celery_conda.sh.before" 2>/dev/null || true
```

Preserve the old repository instead of deleting it:

```sh
mv /home/ubuntu/git /home/ubuntu/git_old_$(date +%Y%m%d_%H%M%S)
```

### 3. Freeze Autoscribe Cron

Temporarily disable only the autoscribe lines while leaving unrelated cron jobs
intact. Save the disabled crontab in the backup directory.

After migration, production autoscribe cron should be restored to:

```cron
@reboot cd ~/git/experiments/client_autoscribe && ~/.local/bin/uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
*/30 * * * * cd ~/git/experiments/client_autoscribe && ~/.local/bin/uv run python client_autoscribe_to_catalogix_cron.py
```

### 4. Stop Production Processes

Verify what will be stopped before stopping it:

```sh
systemctl --user list-units --all 'celery@client_autoscribe*' --no-pager
pgrep -af 'uwsgi.*client_autoscribe|celery.*client_autoscribe'
ss -ltnp '( sport = :4008 )'
```

Stop workers:

```sh
systemctl --user stop \
  celery@client_autoscribe_v2.service \
  celery@client_autoscribe_v2_abfrl.service \
  celery@client_autoscribe_v2_streamoid.service \
  celery@client_autoscribe_v2_misc.service
```

Stop uWSGI:

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
~/.local/bin/uv run uwsgi --stop /home/ubuntu/logs/client_autoscribe_v2.pid
```

Verify:

```sh
pgrep -af 'uwsgi.*client_autoscribe|celery.*client_autoscribe' || true
ss -ltnp '( sport = :4008 )' || true
```

### 5. Move Repository and Recreate `.venv`

Example folder-name cutover:

```sh
mv /home/ubuntu/git /home/ubuntu/git_old_$(date +%Y%m%d_%H%M%S)
mv /home/ubuntu/git2 /home/ubuntu/git
cd /home/ubuntu/git/experiments/client_autoscribe
rm -rf .venv
~/.local/bin/uv sync --frozen
```

Recreating `.venv` is required after folder renames because venv scripts and
shebangs are path-sensitive.

Verify:

```sh
readlink -f .venv/bin/python
head -1 .venv/bin/celery
~/.local/bin/uv lock --check
~/.local/bin/uv sync --frozen --dry-run
.venv/bin/celery --version
.venv/bin/uwsgi --version
```

### 6. Update Cron and Celery Runtime Config

Cron should point to `~/git/experiments/client_autoscribe` and use
`~/.local/bin/uv run`.

Celery env files should use absolute paths:

```sh
WORKING_DIR="/home/ubuntu/git/experiments/client_autoscribe"
CELERY_BIN="/home/ubuntu/git/experiments/client_autoscribe/.venv/bin/celery"
```

Reload user systemd after env/template changes:

```sh
systemctl --user daemon-reload
```

Verify config contains no old production references:

```sh
rg -n '/home/ubuntu/git_old_|/home/ubuntu/git2/experiments/client_autoscribe|/home/ubuntu/miniconda3/envs/client_autoscribe' \
  /home/ubuntu/.config/systemd /home/ubuntu/bin /var/spool/cron /var/spool/cron/crontabs 2>/dev/null || true
```

### 7. Start Services

Start uWSGI:

```sh
cd /home/ubuntu/git/experiments/client_autoscribe
~/.local/bin/uv run uwsgi --ini uwsgi_client_autoscribe_v2.ini
```

Start Celery:

```sh
systemctl --user start \
  celery@client_autoscribe_v2.service \
  celery@client_autoscribe_v2_abfrl.service \
  celery@client_autoscribe_v2_streamoid.service \
  celery@client_autoscribe_v2_misc.service
```

Verify before proceeding:

```sh
ss -ltnp '( sport = :4008 )'
systemctl --user is-active \
  celery@client_autoscribe_v2.service \
  celery@client_autoscribe_v2_abfrl.service \
  celery@client_autoscribe_v2_streamoid.service \
  celery@client_autoscribe_v2_misc.service
```

### 8. Final Production Audit

Run these checks before declaring the migration complete:

```sh
curl -ksS -o /tmp/autoscribe_dashboard.html -w 'status=%{http_code} bytes=%{size_download} time=%{time_total}\n' \
  https://cataloging.streamoid.com/api/autoscribe/vendors/abfrl_lbrd_prod/catalogix-dashboard

cd /home/ubuntu/git/experiments/client_autoscribe
.venv/bin/celery -A client_autoscribe_worker_v2 status --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect active_queues --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect active --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect reserved --timeout=10
.venv/bin/celery -A client_autoscribe_worker_v2 inspect scheduled --timeout=10

sudo rabbitmqctl list_queues -p calm_host name messages messages_ready messages_unacknowledged consumers

systemctl is-active cron nginx rabbitmq-server redis-server mongod
systemctl --user list-units --failed 'celery@client_autoscribe*' --no-pager

rg -n '/home/ubuntu/git_old_|/home/ubuntu/git2/experiments/client_autoscribe|/home/ubuntu/miniconda3/envs/client_autoscribe' \
  /etc/nginx /etc/systemd /home/ubuntu/.config/systemd /home/ubuntu/bin /var/spool/cron /var/spool/cron/crontabs 2>/dev/null || true

ps -eo pid=,ppid=,cmd= | rg '/home/ubuntu/git_old_|/home/ubuntu/git2/experiments/client_autoscribe|/home/ubuntu/miniconda3/envs/client_autoscribe' | rg -v 'rg ' || true
```

Expected completed state:

- Public safe GET endpoints return `200`.
- uWSGI listens on `127.0.0.1:4008`.
- uWSGI executable is `.venv/bin/uwsgi` and cwd is the live repo.
- All four autoscribe Celery workers are active and pingable.
- RabbitMQ autoscribe queues have consumers and no unexpected backlog.
- Cron points to `~/git/experiments/client_autoscribe` and uses `uv run`.
- No live service/config/process references old repo paths or old conda env.
- Backup directory and old repo are preserved.

## Rollback Notes

Rollback should be a deliberate production operation, not an automatic cleanup.
Use only if the new code path fails health checks and cannot be repaired
quickly.

High-level rollback shape:

```sh
# Stop new runtime.
systemctl --user stop \
  celery@client_autoscribe_v2.service \
  celery@client_autoscribe_v2_abfrl.service \
  celery@client_autoscribe_v2_streamoid.service \
  celery@client_autoscribe_v2_misc.service

cd /home/ubuntu/git/experiments/client_autoscribe
~/.local/bin/uv run uwsgi --stop /home/ubuntu/logs/client_autoscribe_v2.pid

# Move current repo aside and restore previous repo name.
mv /home/ubuntu/git /home/ubuntu/git_failed_$(date +%Y%m%d_%H%M%S)
mv /home/ubuntu/git_old_20260630_184627 /home/ubuntu/git

# Restore saved cron/systemd configs from the matching cutover backup.
cp /home/ubuntu/cutover_backup_20260630_184446/celery@.service.before /home/ubuntu/.config/systemd/user/celery@.service
for f in \
  client_autoscribe_v2.conf \
  client_autoscribe_v2_abfrl.conf \
  client_autoscribe_v2_streamoid.conf \
  client_autoscribe_v2_misc.conf
do
  cp "/home/ubuntu/cutover_backup_20260630_184446/$f.before" "/home/ubuntu/bin/$f"
done
crontab /home/ubuntu/cutover_backup_20260630_184446/crontab.before
systemctl --user daemon-reload
```

After any rollback, start the restored services using the commands appropriate
for the restored configuration, then run the same final production audit.

## Key Components

### 1. API Endpoints

#### Main API Endpoints (client_autoscribe_api_v2.py)

**Base URL Pattern**: `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/`

#### Core Endpoints:

1. **POST `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/post`**
   - **Purpose**: Used by clients to upload product data
   - **Functionality**: Accepts product data from external clients
   - **Handler**: `product_post()`

2. **GET `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/export-only-new-catalogix`**
   - **Purpose**: Export new products to Catalogix
   - **Functionality**: Exports products that haven't been pushed to Catalogix yet
   - **Handler**: `export_only_new_catalogix_get()`

3. **POST `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/import-catalogix`**
   - **Purpose**: Import curated data from Catalogix
   - **Functionality**: Receives curated product data back from Catalogix
   - **Handler**: `import_catalogix_data_post()`
   - **Data Format**: JSON with store_uuid, marketplace, and product data array

4. **GET `/api/autoscribe/vendors/{vendor_name}/catalogix-dashboard`**
   - **Purpose**: Internal dashboard for reviewing and managing products
   - **Functionality**: Provides overview of all brands and their status
   - **Handler**: `catalogix_dashboard_get()`

5. **GET `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/catalogix-review`**
   - **Purpose**: Review curated products from Catalogix
   - **Functionality**: Shows products ready for review
   - **Handler**: `catalogix_review_get()`

6. **POST `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/catalogix-mark-reviewed`**
   - **Purpose**: Mark products as reviewed
   - **Functionality**: Approves products for final posting
   - **Handler**: `catalogix_mark_reviewed_post()`

7. **GET `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/catalogix-post-to-client`**
   - **Purpose**: Post reviewed data to client/vendor
   - **Functionality**: Triggers posting of all reviewed and non-hold data to vendor APIs
   - **Handler**: `catalogix_post_to_client_get()`

### 2. Cron Job System

#### client_autoscribe_to_catalogix_cron.py
- **Purpose**: Automated batch processing to push data to Catalogix
- **Frequency**: Every 30 minutes
- **Functionality**: 
  - Identifies new products not yet pushed to Catalogix
  - Converts product data to CSV format
  - Uploads CSV to Catalogix feed system
  - Updates database to mark products as pushed

**Key Functions**:
- `get_brands_sc()`: Gets new style codes for brands
- `get_data()`: Processes and uploads data to Catalogix
- `upload_csv_to_feed()`: Uploads CSV to Catalogix feed ingest API

### 3. Database Operations

#### ClientAutoscribeDB (client_autoscribe_db_v2.py)
- **Purpose**: Manages all database operations
- **Collections**: 
  - `products`: Original product data
  - `live`: Processed product data
  - `catalogix`: Data received from Catalogix
  - `rejects`: Rejected products

**Key Methods**:
- `get_new_products_not_pushed_catalogix()`: Gets products ready for Catalogix
- `get_catalogix_products_for_review_v2()`: Gets products ready for review
- `get_products_to_post()`: Gets products ready for client posting
- `post_to_client()`: Posts data to vendor APIs

### 4. Worker System

#### client_autoscribe_worker_v2.py
- **Purpose**: Background task processing
- **Tasks**:
  - `post_to_client()`: Handles posting to vendor APIs
  - `catalogix_post_to_client()`: Manages Catalogix to client posting
  - `trigger_precompute()`: Triggers preprocessing tasks

## Complete Workflow

### 1. Client Upload Phase
```
Client → POST /api/autoscribe/vendors/{vendor}/brands/{brand}/post
```
- Clients upload product data via the POST endpoint
- Data is stored in the `products` collection
- Products are marked as new and ready for processing

### 2. Automated Catalogix Push (Every 30 minutes)
```
Cron Job → client_autoscribe_to_catalogix_cron.py
```
- Cron job identifies new products not yet pushed to Catalogix
- Converts product data to CSV format with proper mappings
- Uploads CSV to Catalogix feed system via feed ingest API
- Updates database to mark products as "pushed to catalogix"

### 3. Catalogix Curation Phase
```
Catalogix Internal Team → Curates and translates product data
```
- Internal team at Catalogix reviews and curates the uploaded data
- Products are processed through Catalogix's curation system
- Curated data is prepared for import back to client_autoscribe

### 4. Import from Catalogix
```
Catalogix → POST /api/autoscribe/vendors/{vendor}/brands/{brand}/import-catalogix
```
- Catalogix triggers import via the import-catalogix endpoint
- Curated data is received and stored in the `catalogix` collection
- Products are marked as ready for review

### 5. Internal Review Phase
```
Internal Team (Arshiya) → GET /api/autoscribe/vendors/{vendor}/catalogix-dashboard
```
- Internal team accesses the catalogix dashboard
- Reviews curated products from Catalogix
- Can mark products as:
  - **Reviewed**: Approved for posting
  - **On Hold**: Temporarily blocked from posting

### 6. Final Posting to Client
```
Internal Team → GET /api/autoscribe/vendors/{vendor}/brands/{brand}/catalogix-post-to-client
```
- Internal team triggers posting of all reviewed (non-hold) products
- System posts data to vendor APIs using their specific integrations
- Triggers vendor-specific API calls to deliver final curated data
- Updates database to mark products as posted

## Integration Points

### Vendor Integrations
The system supports multiple vendor integrations located in the `integrations/` directory:
- `abfrl_lbrd_prod.py`: ABFRL production integration (Active client abfrl prod)
- `abfrl_test.py`: ABFRL testing integration (Active client abfrl Test)
- `grupo_soma.py`: Grupo Soma integration (Unactive client)
- `farfetch.py`: Farfetch integration (Unactive client)
- `tatacliq.py`: Tata Cliq integration (Unactive client)

Each integration implements a `VendorAdapter` class that handles:
- API authentication
- Data formatting
- Error handling
- Response processing

### Catalogix Integration
- **Feed Upload API**: `https://service.feed-upload.streamoid.com/v1/upload`
- **Feed Ingest API**: `https://service.feed-upload.streamoid.com/v1/feed/{store_uuid}/upload`
- **Store Settings API**: `https://kepler-backend.staging.streamoid.com/v1/store/{store_uuid}`

## Configuration

### Vendor Configuration
- Each vendor has specific configuration for API endpoints, authentication, and data mappings
- Brand-level configurations define product mappings and processing rules
- Store UUIDs map brands to Catalogix stores

### Database Configuration
- - MongoDB collections follow naming patterns:
  - `v_{vendor}_autoscribe`
- MongoDB collections follow naming patterns:
  - `products:{brand}`
  - `catalogix:{brand}`
  - `rejects:{brand}`

## Error Handling


## Monitoring and Reporting

### Logs:

    #### API:
        - tail -f ~/logs/uwsgi/client_autoscribe_v2_log.txt 

    #### Worker (client_autoscribe_v2_abfrl / client_autoscribe_v2 / client_autoscribe_v2_misc / client_autoscribe_v2_streamoid):
        - tail -f ~/logs/{worker}/*.log



### Dashboard Features
- Real-time counts of products in each stage
- Brand-level status overview
- Export capabilities for different product states
- Bulk operations for efficiency

### Teams Integration (Needs to be updated)
- Automated notifications for important events
- Daily status reports
- Error alerts and notifications

## Security Considerations

- API key authentication for vendor integrations
- Store UUID validation for Catalogix operations
- Request ID tracking for audit trails
- Secure handling of product data and images

## Deployment

### Services
- **API Service**: Handles HTTP requests and responses
- **Worker Service**: Processes background tasks
- **Cron Service**: Handles scheduled operations
- **Listener Service**: Monitors for real-time updates

### Configuration Files
- `uwsgi_client_autoscribe_v2.ini`: API service configuration
- `uwsgi_client_autoscribe.ini`: Legacy API configuration
- `uwsgi_autoscribe_listener.ini`: Listener service configuration
- `gunicorn.conf.py`: Gunicorn server configuration

### Worker Configuration Files:
- ls ~/bin/*.conf


# People & Ownership
 
- Stakeholders / Consumers of the Service: ABFRL
- Curation Team: Arshiya Dodrajka
