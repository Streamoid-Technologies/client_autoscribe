from pymongo import DESCENDING


class RPADatabase(object):
    def get_rpa_details(self, coll, rpa_request_id, filename):
        key = '%s:%s' % (rpa_request_id, filename)
        for row in coll.find({'_id': key}):
            return row

    def set_rpa_details(self, coll, rpa_request_id, responses):
        for fname, response in responses.items():
            key = '%s:%s' % (rpa_request_id, fname)
            # response['_id'] = key
            response['rpa_request_id'] = rpa_request_id
            response['filename'] = fname
            coll.update_one({'_id': key}, {'$set': response}, upsert=True)

    def get_rpa_status(self, coll, condition=None, offset=None, limit=None):
        condition = condition if condition is not None else {}
        cursor = coll.find(condition).sort('ts', DESCENDING)
        if offset is not None:
            cursor = cursor.skip(offset)
        if limit is not None:
            cursor = cursor.limit(limit)
        return [row for row in cursor]
