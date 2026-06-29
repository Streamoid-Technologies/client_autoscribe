import os

home_dir = os.getenv('HOME')
log_file_path = os.path.join(home_dir, 'logs/gunicorn/rpa_api.txt')
pid_file_path = os.path.join(home_dir, 'logs/rpa_api.pid')
os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

# gunicorn settings
bind = ['127.0.0.1:8000']
workers = 4
worker_class = 'uvicorn.workers.UvicornWorker'
timeout = 300
accesslog = log_file_path
errorlog = log_file_path
capture_output = True
disable_redirect_access_to_syslog = True
loglevel = 'info'
daemon = True
pidfile = pid_file_path
enable_stdio_inheritance = True
