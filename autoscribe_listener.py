import os
import sys
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(
    format=
    '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO)

import json

import werkzeug
import werkzeug.exceptions
from werkzeug.routing import Map, Rule
from werkzeug.wrappers import Request, Response

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.listener_db import ListenerDB


class AutoscribeListener(ListenerDB):
    def __init__(self, mongo_host):
        super(AutoscribeListener, self).__init__(mongo_host)
        self.url_map = Map(
            [
                # product push
                Rule('/slack/autoscribe-listener', endpoint='autoscribe_listener', methods=['POST'])
            ],
            redirect_defaults=False,
            strict_slashes=False)

    def dispatch_request(self, request):
        """Indirection layer which gives us automatic 404 for unknown paths.
        """
        adapter = self.url_map.bind_to_environ(request.environ)
        try:
            endpoint, values = adapter.match()
            return getattr(self, endpoint)(request, **values)
        except werkzeug.exceptions.HTTPException as e:
            return e

    def __call__(self, environ, start_response):
        """Implementation of the standard wsgi callable interface."""
        response = self.dispatch_request(Request(environ))
        return response(environ, start_response)

    def autoscribe_listener(self, request):
        """
        {'api_app_id': 'A0153322475',
         'authed_users': ['U0151J70R7F'],
         'event': {'bot_id': 'BMNAW2L95',
                   'channel': 'CN001732L',
                   'channel_type': 'channel',
                   'event_ts': '1591705264.010600',
                   'subtype': 'bot_message',
                   'text': '*Data posted to ABFRL :*\n'
                           '<https://db.tools.streamoid.com/feed/insecure/get_data/?path=/autoscribe/v_louis_philippe_autoscribe_2020_06_09_12_21_04.json>\n'
                           '*request_id*:`1693`\n'
                           '*Number of products*:`1`',
                   'ts': '1591705264.010600',
                   'type': 'message',
                   'username': 'v_louis_philippe_autoscribe'},
         'event_id': 'Ev0151L21CRK',
         'event_time': 1591705264,
         'team_id': 'T04GRE07V',
         'token': 'rDWbh7PPZjArCWV1LuFU7WRp',
         'type': 'event_callback'}
        :param request:
        :return:
        """
        data = json.loads(request.data.decode())
        logger.info(data)
        response = {}
        if data['type'] == 'event_callback':
            self.save_event(data)
            self.handle_event(data)
        elif data['type'] == 'url_verification':
            response['challenge'] = data['challenge']
        return Response(json.dumps(response), mimetype='application/json')


application = AutoscribeListener('localhost')

if __name__ == '__main__':
    from werkzeug.serving import run_simple

    run_simple('0.0.0.0', 4006, application)
