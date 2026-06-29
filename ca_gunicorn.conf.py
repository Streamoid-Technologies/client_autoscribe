import os

home_dir = os.getenv('HOME')
log_dir = os.path.join(home_dir, 'logs/gunicorn')
os.makedirs(log_dir, exist_ok=True)

log_file_path = os.path.join(log_dir, 'client_autoscribe_v2_log.txt')
pid_file_path = os.path.join(log_dir, 'client_autoscribe_v2.pid')


# Gunicorn settings
bind = ['127.0.0.1:4008']  # same port as uWSGI
workers = 4  # same as uWSGI 'processes'
worker_class = 'uvicorn.workers.UvicornWorker'  # for async FastAPI
timeout = 300  # matches 'harakiri'

accesslog = log_file_path
errorlog = log_file_path
capture_output = True
loglevel = 'info'

daemon = True
pidfile = pid_file_path
enable_stdio_inheritance = True