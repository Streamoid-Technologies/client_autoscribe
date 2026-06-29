import sys
import logging

logger = logging.getLogger(__name__)

from pymongo import MongoClient

if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    vendor = sys.argv[1]
    brand = sys.argv[2]

    db_name = 'v_%s_autoscribe' % vendor
    coll_name = 'cataloging:%s' % brand
    logger.info('%s %s', db_name, coll_name)
    client = MongoClient()
    coll = client[db_name][coll_name]

    # 1. detect requests which might be incorrectly pushed
    request_ids = coll.distinct('output.RequestID', {'push_time': {'$exists': False}, 'pushed': {'$eq': True}})
    logger.info(request_ids)

    # 2. mark pushed=false for all such requests and re-push
    for request_id in request_ids:
        if request_id is None:
            continue
        logger.info(request_id)
        coll.update_many({'output.RequestID': request_id, 'pushed': True}, {'$set': {'pushed': False}})