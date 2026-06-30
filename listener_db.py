import argparse
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

from pymongo import MongoClient, DESCENDING
from tqdm import tqdm
from datetime import datetime

try:
    from client_autoscribe.slack_config import get_slack_token
except ImportError:
    from slack_config import get_slack_token


def convert_to_ts(date1):
    if date1 is None:
        return
    return int(datetime.strptime(date1, '%Y-%m-%d').timestamp())


class ListenerDB(object):
    def __init__(self, mongo_host):
        self.client = MongoClient(mongo_host)
        self.cataloging_host = 'https://cataloging.streamoid.com'
        self.vendor_tokens = self.get_vendor_tokens()
        logger.info(self.vendor_tokens)

        # mongodb specific
        self.listener_db = 'autoscribe_listener'
        self.messages_coll = 'messages'
        self.autoscribe_coll = 'autoscribe'
        self.rejects_coll = 'rejects'
        self.events_coll = 'events'

        # slack specific
        self.bot_user_token = get_slack_token('bot_user_token', 'SLACK_BOT_USER_TOKEN')
        self.autoscribe_channel = 'CN001732L'
        self.rejects_channel = 'CQUMZU0LR'

    def save_event(self, data):
        coll = self.client[self.listener_db][self.events_coll]
        data['_id'] = data['event_id']
        coll.replace_one({'_id': data['_id']}, data, upsert=True)

    def get_vendor(self, username):
        return username.replace('v_', '').replace('_autoscribe', '')

    def post_rejects(self, vendor, data):
        url = self.cataloging_host + '/api/autoscribe/abfrl/reject/' + vendor
        data = {'data': json.dumps(data)}
        headers = {'vendor-token': self.vendor_tokens[vendor]}
        logger.info('%s %s %s', url, data, headers)
        r = requests.post(url, data=data, headers=headers, timeout=60)
        if r.ok:
            logger.info(r.json())
        else:
            logger.error(r.status_code)

    def get_vendor_tokens(self):
        url = self.cataloging_host + '/api/autoscribe/abfrl/vendor-tokens'
        r = requests.get(url, timeout=30)
        if r.ok:
            return r.json()

    def handle_event(self, data):
        event = data['event']
        channel = event['channel']
        text = event['text']
        username = event.get('username', '')
        if channel == self.autoscribe_channel:
            self.save_messages([event], self.autoscribe_coll)
            self.handle_autoscribe_post(username, text)
        elif channel == self.rejects_channel:
            self.save_messages([event], self.rejects_coll)
            self.handle_rejects_post(username, text)

    def save_messages(self, msgs, collection):
        coll = self.client[self.listener_db][collection]
        for msg in msgs:
            msg['_id'] = msg['ts']
            coll.update_one({'_id': msg['_id']}, {'$set': msg}, upsert=True)

    def handle_autoscribe_post(self, username, text):
        try:
            logger.info('%s %s', username, text)
            lines = text.strip()
            if 'Streamoid' in lines:
                url = lines[lines.find('https'):].replace('`', '').replace('>', '')
                logger.info(url)
                data = self.fetch_data(url)
                msg = data['data']['StyleCodes']
                vendor = self.get_vendor(username)
                self.post_autoscribe(vendor, msg)
        except Exception as e:
            logger.exception(str(e))

    def post_autoscribe(self, vendor, data):
        url = self.cataloging_host + '/api/autoscribe/abfrl/post/' + vendor
        data = {'data': json.dumps(data)}
        headers = {'vendor-token': self.vendor_tokens[vendor]}
        logger.info('%s %s %s', url, data, headers)
        r = requests.post(url, data=data, headers=headers, timeout=60)
        if r.ok:
            logger.info(r.json())
        else:
            logger.error(r.status_code)

    def fetch_data(self, url):
        r = requests.get(url, timeout=60)
        if r.ok:
            return r.json()

    def handle_rejects_post(self, username, text):
        try:
            logger.info('%s %s', username, text)
            lines = text.strip().split('\n')
            if len(lines) > 1:
                msg = json.loads(lines[1].replace('`', ''))
                vendor = self.get_vendor(username)
                self.post_rejects(vendor, msg)
        except Exception as e:
            logger.exception(str(e))

    def _get_conversations(self, channel, cursor, limit=None, oldest=None, latest=None):
        if not self.bot_user_token:
            logger.warning('SLACK_BOT_USER_TOKEN is not configured; skipping Slack conversation fetch')
            return

        url = 'https://slack.com/api/conversations.history'
        headers = {'Authorization': 'Bearer %s' % self.bot_user_token}
        params = {'channel': channel}
        if limit is not None:
            params['limit'] = limit
        if oldest is not None:
            params['oldest'] = oldest
        if latest is not None:
            params['latest'] = latest
        if cursor is not None:
            params['cursor'] = cursor
        r = requests.get(url, headers=headers, params=params)
        if r.ok:
            return r.json()
        else:
            logger.error(r.status_code)

    def get_conversations(self, channel, limit=None, oldest=None, latest=None):
        messages = []
        has_more = True
        cursor = None
        while has_more:
            response = self._get_conversations(channel, cursor, limit, oldest, latest)
            if response is None:
                break
            # logger.info(response)
            new_messages = response['messages']
            logger.info('Got %d messages', len(new_messages))
            # logger.info(new_messages[0])
            messages.extend(new_messages)
            has_more = response['has_more']
            cursor = response.get('response_metadata', {}).get('next_cursor', None)
        return messages

    def migrate(self):
        coll1 = self.client[self.listener_db][self.messages_coll]
        coll2 = self.client[self.listener_db][self.events_coll]
        for row in coll1.find():
            row['_id'] = row['event_id']
            coll2.insert_one(row)

    def get_autoscribe_conversations(self, oldest=None, latest=None):
        messages = self.get_conversations(self.autoscribe_channel, oldest=oldest, latest=latest)
        logger.info('Got %d autoscribe messages', len(messages))
        self.save_messages(messages, self.autoscribe_coll)

    def get_rejects_conversations(self, oldest=None, latest=None):
        messages = self.get_conversations(self.rejects_channel, oldest=oldest, latest=latest)
        logger.info('Got %d rejects messages', len(messages))
        self.save_messages(messages, self.rejects_coll)

    def push_autoscribe_messages(self, limit=None, oldest=None, latest=None):
        coll = self.client[self.listener_db][self.autoscribe_coll]
        condition = {}
        if oldest is not None:
            condition['$gt'] = str(oldest)
        if latest is not None:
            condition['$lt'] = str(latest)
        if len(condition) > 0:
            condition = {'_id': condition}
        iter = coll.find(condition)
        if limit is not None:
            iter = iter.sort('_id', DESCENDING).limit(limit)
        for row in tqdm(iter):
            logger.info(row)
            self.handle_autoscribe_post(row.get('username', ''), row['text'])

    def push_rejects_messages(self, limit=None, oldest=None, latest=None):
        coll = self.client[self.listener_db][self.rejects_coll]
        condition = {}
        if oldest is not None:
            condition['$gt'] = str(oldest)
        if latest is not None:
            condition['$lt'] = str(latest)
        if len(condition) > 0:
            condition = {'_id': condition}
        iter = coll.find(condition)
        if limit is not None:
            iter = iter.sort('_id', DESCENDING).limit(limit)
        for row in tqdm(iter):
            logger.info(row)
            self.handle_rejects_post(row.get('username', ''), row['text'])


if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('--oldest', help='2020-10-13')
    parser.add_argument('--latest', help='2020-10-14')
    parser.add_argument('--limit', default=150, type=int)
    args = parser.parse_args()

    obj = ListenerDB('localhost')
    # print(obj.get_conversations())
    # obj.migrate()
    limit = args.limit

    oldest_ts = convert_to_ts(args.oldest)
    latest_ts = convert_to_ts(args.latest)
    # obj.get_autoscribe_conversations(oldest=oldest_ts, latest=latest_ts)
    # obj.push_autoscribe_messages(oldest=oldest_ts, latest=latest_ts)
    # obj.push_autoscribe_messages(limit)
    #
    # obj.get_rejects_conversations()
    obj.push_rejects_messages(limit)
