#!/usr/bin/env python3
import argparse
import json
import sys

from pymongo import MongoClient


def get_db_name(vendor_name):
    return 'v_' + vendor_name.lower().strip().replace(' ', '_').replace('-', '_') + '_autoscribe'


def main():
    parser = argparse.ArgumentParser(description='Print autoscribe brand config from MongoDB.')
    parser.add_argument('--mongo-host', default='localhost')
    parser.add_argument('--vendor', default='abfrl_test')
    parser.add_argument('--brand', default='allen_solly')
    parser.add_argument('--source', action='store_true', help='Print MongoDB source to stderr.')
    parser.add_argument('--pretty', action='store_true', help='Pretty-print JSON output.')
    args = parser.parse_args()

    db_name = get_db_name(args.vendor)
    collection_name = 'brands'

    if args.source:
        print(
            'source: mongodb://%s/%s.%s document {"_id": "%s"}'
            % (args.mongo_host, db_name, collection_name, args.brand),
            file=sys.stderr,
        )

    client = MongoClient(args.mongo_host)
    config = client[db_name][collection_name].find_one({'_id': args.brand})

    if config is None:
        raise SystemExit('No config found for vendor=%s brand=%s' % (args.vendor, args.brand))

    if args.pretty:
        print(json.dumps(config, indent=2, sort_keys=True, default=str))
    else:
        print(json.dumps(config, default=str))


if __name__ == '__main__':
    main()
