import logging

from copy import copy
from pymongo import MongoClient

from client_autoscribe.rule_parser import matches

logger = logging.getLogger(__name__)


def _escape(data, mapping):
    if isinstance(data, str):
        for k, v in mapping.items():
            data = data.replace(k, v)
        return data
    elif isinstance(data, dict):
        return {_escape(k, mapping): _escape(v, mapping) for k, v in data.items()}
    elif isinstance(data, list):
        return [_escape(x, mapping) for x in data]
    elif isinstance(data, float):
        return data
    elif isinstance(data, bool):
        return data
    elif isinstance(data, int):
        return data
    elif data is None:
        return data

    logger.warning('Unknown type in escape: %s [%s]', data, type(data))
    return data


def escape(data):
    return _escape(data, mapping={'.': '<dot>'})


def unescape(data):
    return _escape(data, mapping={'<dot>': '.'})


def compute_index(mappings):
    index = {}
    negations = []
    for key, val in mappings.items():
        if isinstance(val, str):
            if val not in index:
                index[val] = []
            index[val].append(key)
        elif isinstance(val, dict):
            for key1, val1 in val.items():
                if key1 not in index:
                    index[key1] = []
                index[key1].append(key)
                if not val1:
                    negations.append(key)
        elif isinstance(val, list):
            for val1 in val:
                if isinstance(val1, str):
                    if val1 not in index:
                        index[val1] = []
                    index[val1].append(key)
                elif isinstance(val1, dict):
                    for key2, val2 in val1.items():
                        if key2 not in index:
                            index[key2] = []
                        index[key2].append(key)
                        if not val2:
                            negations.append(key)

    return {k: list(set(v)) for k, v in index.items()}, list(set(negations))


class OntologyDB(object):
    def __init__(self, mongo_host, type='common'):
        self.mongo_host = mongo_host
        self.client = MongoClient(mongo_host)
        self.type = type
        self.ontology_db = 'ontologies'
        self.ontology_coll = 'ontology'

    def get_ontology_collection(self, name):
        if self.type == 'common':
            return self.client[self.ontology_db][name]
        else:
            return self.client[name][self.ontology_coll]

    def write_ontology(self, coll, all_data):
        for _id in all_data:
            id_field = {'_id': _id}
            coll.replace_one(id_field, all_data[_id], upsert=True)

    def get_parsed_ontology(self, all_data):
        pass

    def push_ontology(self, all_data, ontology_name):
        coll = self.get_ontology_collection(ontology_name)
        coll.delete_many({'type': 'conditional'})
        data, errors = self.get_parsed_ontology(escape(all_data))
        logger.info("Writing ontology " + ontology_name)
        self.write_ontology(coll, data)
        return errors

    def get_ontologies(self):
        db = self.client[self.ontology_db]
        return [x for x in db.list_collection_names() if '.' not in x]

    def _matches_v2(self, tags, condition):
        condition2 = {tag: True for tag in tags}
        return condition == condition2

    def get_tags_by_id(self, coll, id1):
        tags = []
        for row in coll.find({'_id': id1}):
            tags.extend(row.get('tags', []))
            for set1 in row.get('include', []):
                tags.extend(self.get_tags_by_id(coll, set1))
        return tags

    def set_ontology_conditionals(self, ontology, tags):
        coll = self.get_ontology_collection(ontology)
        tags = escape(tags)
        return coll.update_one({'_id': 'root'}, {'$addToSet': {'tags': tags},
                                                 '$set': {'type': 'conditional',
                                                          'include': []}}, upsert=True)

    def get_ontology_conditionals(self, ontology, tags, show_all=False):
        coll = self.get_ontology_collection(ontology)
        tags = escape(tags)
        values = []
        for row in coll.find({'type': 'conditional'}):
            condition = row.get('condition', {})
            if show_all:
                values.extend(self.get_tags_by_id(coll, row['_id']))
            else:
                match = self._matches_v2(tags, condition)
                if match:
                    logger.debug('%s matches %s', tags, condition)
                    values.extend(row.get('tags', []))

        values = list(set(values))
        values = unescape(values)
        return sorted(values)

    def get_ontology_implications(self, target_ontology, source_ontology):
        coll = self.get_ontology_collection(target_ontology)
        mappings = {}
        index = {}
        negations = []
        for row in coll.find({'type': 'implication', 'ontology': source_ontology}):
            mappings = row.get('mappings', {})
            index = row.get('index', {})
            negations = row.get('negations', [])
        return unescape(mappings), unescape(index), unescape(negations)

    def set_ontology_implications(self, target_ontology, source_ontology, mappings):
        coll = self.get_ontology_collection(target_ontology)
        condition = {'_id': source_ontology, 'type': 'implication', 'ontology': source_ontology}
        row = copy(condition)
        row['mappings'] = escape(mappings)
        index, negations = compute_index(mappings)
        row['index'] = escape(index)
        row['negations'] = escape(negations)
        return coll.replace_one(condition, row, upsert=True)

    def translate(self, target_ontology, source_ontology, source_tags):
        if target_ontology == source_ontology:
            return source_tags
        mappings, index, negations = self.get_ontology_implications(target_ontology, source_ontology)
        out = []
        for source_tag in source_tags:
            target_tags = index.get(source_tag, []) + negations
            for target_tag in target_tags:
                source_condition = mappings[target_tag]
                if matches(source_tags, source_condition):
                    out.append(target_tag)
        return sorted(list(set(out)))

    def restrict_tags(self, ontology, tags):
        tags1 = []
        depth = 0
        while depth < 5:
            allowed_tags = self.get_ontology_conditionals(ontology, tags1)
            logger.info(allowed_tags)
            if len(allowed_tags) < 1:
                break
            common = set(allowed_tags) & set(tags)
            tags1.extend(list(common))
            if len(tags1) < 1:
                break
            depth += 1
        return tags1 if len(tags1) > 0 else tags
