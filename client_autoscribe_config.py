import logging
from celery import Celery

logger = logging.getLogger(__name__)


class ClientAutoscribeConfig(object):
    broker_url = 'pyamqp://streamoid:650d6d75@localhost:5672/calm_host'
    result_backend = 'redis://localhost:6379/0'
    task_acks_late = True
    worker_prefetch_multiplier = 1
    result_accept_content = ['application/json']
    worker_log_format = '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s'
    task_routes = {'client_autoscribe_worker.*': {'queue': 'client_autoscribe'},
                   'client_autoscribe_worker_v2.queue_post_to_teams': {'queue': 'client_autoscribe_v2'},
                   'client_autoscribe_worker_v2.*': {'queue': 'client_autoscribe_v2_misc'}}
    task_annotations = {'client_autoscribe_worker_v2.queue_post_to_teams': {'rate_limit': '1/s'}}


# celery
app = Celery()
app.config_from_object(ClientAutoscribeConfig)

rpa_dir = '/tmp'