import json
import logging
import os

logger = logging.getLogger(__name__)


def get_slack_config_path():
    return os.environ.get(
        'CLIENT_AUTOSCRIBE_SLACK_CONFIG',
        os.path.join(os.path.dirname(__file__), 'slack_config.json')
    )


def load_slack_config():
    config_path = get_slack_config_path()
    if not os.path.exists(config_path):
        return {}

    with open(config_path) as f:
        return json.load(f)


def get_slack_token(config_key, env_var):
    token = os.environ.get(env_var)
    if token:
        return token

    try:
        return load_slack_config().get(config_key)
    except Exception as e:
        logger.warning('Failed to load Slack config: %s', e)
        return None
