import os
import sys
import logging

logger = logging.getLogger(__name__)

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.rule_parser import parse_condition, matches, simplify, check_condition


def get_db_name(vendor_name):
    return 'v_' + vendor_name.lower().strip().replace(' ', '_').replace('-', '_') + '_autoscribe'


def tags_to_attrvals(tags):
    attrvals = {}
    for tag in tags:
        words = tag.split(':', 1)
        attrvals[words[0]] = words[1]
    return attrvals


def split_word(x):
    buf = ''
    last = None
    split_buf = []
    for i in x:
        this = i.isdigit()
        if last is not None:
            if this == last:
                buf += i
            else:
                split_buf.append(buf)
                buf = i
        else:
            buf = i
        last = this
    if len(buf) > 0:
        split_buf.append(buf)
    return split_buf


def parse_material(material):
    # remove commas
    material = material.replace(',', ' ')
    # remove "and"
    words = [x for x in material.strip().split(' ') if len(x) > 0 and x not in ['and']]
    # remove "%"
    words = [x.replace('%', '') for x in words]
    # remove empty words
    words = [x for x in words if len(x) > 0]
    # split words
    words1 = []
    for i in words:
        words1.extend(split_word(i))
    out = {}
    print(words1)
    key = None
    value = None
    # <percent> <words>, <percent> <words>, ...
    for i in words1:
        try:
            val = float(i)
            is_float = True
        except ValueError:
            val = i
            is_float = False

        if is_float:
            if key is not None and value is not None:
                out[key] = value
            # reset key and value
            value = val
            key = None
        else:
            if key is None:
                key = val
            else:
                key += ' ' + val

        # print(out, key, val, is_float)

    if key is not None and value is not None:
        out[key] = value

    # print(out)
    out1 = [x for x in out.items() if x[1] <= 100.0 or x[0].lower() not in ['gsm']]
    return sorted(out1, key=lambda x: x[1], reverse=True)


def parse_non_attr_condition(cond):
    out = None
    if cond.startswith('!'):
        out = {x.strip(): False for x in cond[1:].strip().split(',') if len(x.strip()) > 0}
    else:
        out = [{x.strip(): True} for x in cond.strip().split(',') if len(x.strip()) > 0]
    return simplify(out)


def prefix_attribute(attr, cond):
    if isinstance(cond, str):
        return attr + ':' + cond
    elif isinstance(cond, dict):
        return {attr + ':' + k: v for k, v in cond.items()}
    elif isinstance(cond, list):
        return [prefix_attribute(attr, x) for x in cond]


def match_fabric(material, pfs):
    ans = True
    # logger.info('%s %s', material, pfs)
    for i in range(min(len(material), len(pfs))):
        cond1, comp1 = material[i]
        cond2 = pfs[i]
        # logger.info('%s %s %s', cond1, comp1, cond2)
        if cond2['PF_min'] is not None and comp1 < cond2['PF_min']:
            ans = False
            break
        if cond2['PF_max'] is not None and comp1 > cond2['PF_max']:
            ans = False
            break
        if not matches(cond1, cond2['PF']):
            ans = False
            break
    return ans


class CustomRulesDB(object):
    def __init__(self, mongo_client):
        self.client = mongo_client
        self.custom_rules_coll_pattern = 'custom_rules:%s'

        # record _id
        self.rule_type_exact = 'exact'
        self.rule_type_conditional = 'conditional'
        self.rule_type_category_based = 'category based'

    def parse_sheets(self, f, vendor_name, ontology, all_tags, brand_name=None):
        data = pd.read_excel(f, dtype=str, sheet_name=None, keep_default_na=False)
        all_errors = []
        db_name = get_db_name(vendor_name)
        coll = self.get_column(db_name, ontology, brand_name)
        for sub_sheet_name, sub_sheet_df in data.items():
            if sub_sheet_name == self.rule_type_exact:
                out, errors = self.parse_exact_rules(sub_sheet_df, all_tags)
            elif sub_sheet_name == self.rule_type_conditional:
                out, errors = self.parse_conditional(sub_sheet_df, all_tags)
            elif sub_sheet_name == self.rule_type_category_based:
                out, errors = self.parse_category_based(sub_sheet_df, all_tags)
            else:
                error = 'Invalid sub-sheet name: %s' % sub_sheet_name
                logger.warning(error)
                all_errors.append(error)
                continue

            if len(errors) > 0:
                for err in errors:
                    err['sub_sheet_name'] = sub_sheet_name
                logger.error(errors)
                all_errors.extend(errors)
                continue

            # save to DB
            coll.replace_one({'_id': sub_sheet_name}, {'rules': out}, upsert=True)

        return all_errors

    def parse_exact_rules(self, df, all_tags):
        out = []
        errors = []
        for idx, row in df.iterrows():
            input_attr = row['Input-attribute'].strip()
            output_attr = row['Output-attribute'].strip()
            condition = parse_condition(row['Condition'].strip())
            if len(input_attr) < 1 or len(output_attr) < 1:
                continue
            error = check_condition(condition, all_tags)
            if len(error) > 0:
                errors.append({'line': idx + 2, 'invalid_tags': error})

            out.append({'input_attr': input_attr, 'output_attr': output_attr, 'condition': condition})

        return out, errors

    def get_column(self, db_name, ontology, brand_name, write=True):
        coll_names = self.client[db_name].list_collection_names()
        coll_name = self.custom_rules_coll_pattern % ontology
        if brand_name is not None:
            coll_name1 = coll_name + ':' + brand_name
            if write or coll_name1 in coll_names:
                logger.info(coll_name1)
                return self.client[db_name][coll_name1]
        logger.info(coll_name)
        return self.client[db_name][coll_name]

    def get_rules(self, vendor_name, ontology, rule_type, brand_name=None):
        db_name = get_db_name(vendor_name)
        coll = self.get_column(db_name, ontology, brand_name, write=False)
        for row in coll.find({'_id': rule_type}):
            return row['rules']
        return []

    def apply_exact_rules(self, vendor_name, ontology, tags, vendor_tags, brand_name=None):
        attrvals = tags_to_attrvals(vendor_tags)
        rules = self.get_rules(vendor_name, ontology, self.rule_type_exact, brand_name)
        out = []
        for rule in rules:
            attr = rule['input_attr']
            if attr in attrvals and matches(tags, rule['condition']):
                out.append(rule['output_attr'] + ':' + attrvals[attr])
                logger.info(
                    f"EXACT MATCHED: output_attr: {rule.get('output_attr', None)}, attrvals: {attrvals.get(attr, None)}")
            elif rule.get('output_attr', None) == 'Manufacturer info':
                logger.info(
                    f"EXACT NOT MATCHED: output_attr: {rule.get('output_attr', None)}, attrvals: {attrvals.get(attr, None)}")
        return out

    def parse_conditional(self, df, all_tags):
        out = []
        errors = []
        for idx, row in df.iterrows():
            input_attr = row['Input-attribute'].strip()
            input_val = parse_non_attr_condition(row['Input-value'].strip())

            output_attr = row['Output-attribute'].strip()
            output_val = row['Output-value'].strip()

            if len(input_attr) < 1 or len(output_attr) < 1:
                continue

            source_condition = parse_condition(row['Condition'].strip())
            error = check_condition(source_condition, all_tags)
            if len(error) > 0:
                errors.append({'line': idx + 2, 'invalid_tags': error})
                continue

            vendor_condition = prefix_attribute(input_attr, input_val)
            target = output_attr + ':' + output_val
            out.append({'target': target, 'source_condition': source_condition, 'vendor_condition': vendor_condition})

        return out, errors

    def apply_conditional_rules(self, vendor_name, ontology, tags, vendor_tags, brand_name=None):
        rules = self.get_rules(vendor_name, ontology, self.rule_type_conditional, brand_name)
        out = []
        for rule in rules:
            if matches(tags, rule['source_condition']) and matches(vendor_tags, rule['vendor_condition']):
                out.append(rule['target'])
        return out

    def parse_category_based(self, df, all_tags):
        out = []
        errors = []
        prev = ''
        for idx, row in df.iterrows():
            output_attr = row['Attribute'].strip()
            if len(output_attr) < 1:
                output_attr = prev
            else:
                prev = output_attr
            output_val = row['Value'].strip()
            if len(row['Condition'].strip()) < 1:
                continue
            condition = parse_condition(row['Condition'].strip())
            error = check_condition(condition, all_tags)
            if len(error) > 0:
                errors.append({'line': idx + 2, 'invalid_tags': error})
                continue

            target = output_attr + ':' + output_val
            out.append({'target': target, 'condition': condition})

        return out, errors

    def apply_category_based_rules(self, vendor_name, ontology, tags, brand_name=None):
        rules = self.get_rules(vendor_name, ontology, self.rule_type_category_based, brand_name)
        out = []
        for rule in rules:
            if matches(tags, rule['condition']):
                out.append(rule['target'])
        return out

    def apply_rules(self, vendor_name, ontology, tags, vendor_tags, brand_name=None):
        out = []
        exact = self.apply_exact_rules(vendor_name, ontology, tags, vendor_tags, brand_name)
        logger.info('exact: %s', exact)
        out.extend(exact)
        conditional = self.apply_conditional_rules(vendor_name, ontology, tags, vendor_tags, brand_name)
        logger.info('conditional: %s', conditional)
        out.extend(conditional)
        category_based = self.apply_category_based_rules(vendor_name, ontology, tags, brand_name)
        logger.info('category based: %s', category_based)
        out.extend(category_based)
        return out

    def parse_fabric_sheets(self, f, vendor_name, ontology, all_tags, brand_name=None):
        data = pd.read_excel(f, dtype=str, sheet_name=None, keep_default_na=False)
        all_errors = []
        db_name = get_db_name(vendor_name)
        coll = self.get_column(db_name, ontology, brand_name)
        for sub_sheet_name, sub_sheet_df in data.items():
            rules, errors = self.parse_fabric_rules(sub_sheet_name, sub_sheet_df, all_tags)
            if len(errors) > 0:
                all_errors.extend(errors)
                continue
            # save to DB
            coll.replace_one({'_id': sub_sheet_name}, {'rules': rules, 'type': 'fabric_rules'}, upsert=True)

        return all_errors

    def parse_fabric_rules(self, sub_sheet_name, sub_sheet_df, all_tags):
        rules = []
        logger.info(sub_sheet_df.columns)
        errors = []
        for idx, row in sub_sheet_df.iterrows():
            if len(row[sub_sheet_name].strip()) < 1 or len(row['Category Filter'].strip()) < 1:
                continue
            target = sub_sheet_name + ':' + row[sub_sheet_name].strip()
            condition = parse_condition(row['Category Filter'].strip())
            error = check_condition(condition, all_tags)
            if len(error) > 0:
                errors.append({'sub_sheet_name': sub_sheet_name, 'line': idx + 2, 'invalid_tags': error})
                continue

            out = {'target': target, 'condition': condition}
            pfs = []
            for i in range(3):
                field = 'PF%d' % (i + 1)
                min_field = row[field + '_min'].strip()
                max_field = row[field + '_max'].strip()
                out1 = {'PF': parse_non_attr_condition(row[field].strip()),
                        'PF_min': int(min_field) if len(min_field) > 0 else None,
                        'PF_max': int(max_field) if len(max_field) > 0 else None}
                pfs.append(out1)
            out['PFs'] = pfs
            out['priority'] = sum([1 for x in pfs if len(x['PF']) > 0])
            rules.append(out)
        rules = sorted(rules, key=lambda x: x['priority'], reverse=True)
        return rules, errors

    def apply_fabric_rules(self, vendor_name, ontology, tags, vendor_tags, brand_name=None):
        attrvals = tags_to_attrvals(vendor_tags)
        logger.info(attrvals)
        if 'Material' not in attrvals:
            return []
        material = parse_material(attrvals['Material'])
        logger.info(material)

        db_name = get_db_name(vendor_name)
        coll = self.get_column(db_name, ontology, brand_name, write=False)
        out = []
        for row in coll.find({'type': 'fabric_rules'}):
            for rule in row['rules']:
                if matches(tags, rule['condition']):
                    if match_fabric(material, rule['PFs']):
                        # logger.info('%s %s %s', tags, material, rule)
                        out.append(rule['target'])

        return out


def test_non_image(db, ontology, tags, vendor_tags):
    # product = {
    #     "_id": "VHTFWSLBY12380",
    #     "StyleCode": "VHTFWSLBY12380",
    #     "product_uuid": "53319c862951425c98efb6365f8b28f8",
    #     "Material": "100% Wool",
    #     "Season": "2001",
    #     "RequestID": 1774,
    #     "Manufacturer info": "Aditya Birla Fashion and Retai,r  288/2, Building No,Retail Ltd),Begur,-560068,Bangalore,Karnataka,India",
    #     "Weight in gms": "300",
    #     "Brand": "Van Heusen",
    #     "Fit": "Slim Fit",
    #     "last_modified": "2020-06-10 07:10:49",
    #     "Brand Fit": "Slim Fit",
    #     "Package Contents": "1",
    #     "SubBrand": "Van Heusen"
    # }
    # vendor_tags = [k + ':' + str(v) for k, v in product.items() if ':' not in str(v)]
    print(db.apply_rules('abfrl_lbrd_prod', ontology, tags, vendor_tags))


def test_fabric(db, vendor_name, brand_name, tags):
    # vendor_tags = ['Material:100% Cotton']
    vendor_tags = ["Material:64% Polyester, 34% Viscose and 2% Spandex"]
    print(db.apply_fabric_rules(vendor_name, brand_name, tags, vendor_tags))


if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    db = CustomRulesDB('localhost')
    # ontology = 'Myntra-MP'
    # ontology = 'tatacliq-mp'
    ontology = 'amazon-mp'
    tags = ['Detail:button', 'Print Setting:all-over', 'Features:n/a', 'Blazers Type:single-breasted', 'Unisex:no',
            'Hemline style:straight', 'Neckline:n/a', 'Belt Included:no', 'Pattern:checks', 'Occasion:formal',
            'Length:hip', 'Color:brown', 'Color 2:black', 'Construction Technique:woven', 'Department:men',
            'Sleeve Type:regular', 'Pocket:yes', 'Country of Origin:india', 'Number of Pockets:3', 'Ethnicity:western',
            'Combo:no', 'Neck Depth:regular', 'Pack Of:1', 'Combo of:1', 'Sleeve Length:full-sleeve', 'Style:regular',
            'Lapel Type:notch', 'Size Group:regular', 'Lining:yes', 'Activity:business', 'Vent:side-vent',
            'Reversible:no', 'Category:Blazers', 'Pocket Type:slit-pocket', 'Neck Width:regular', 'Closure Type:button',
            'Pack:single', 'Suit Front:single-breasted-2-button', 'Number of Buttons:2']
    vendor_tags = ['batch_number:1', 'Season:1902', 'Package Contents:1', 'RequestID:1804', '_id:ASBZWSLFD74712',
                   'Brand:Allen Solly',
                   'Manufacturer info:Aditya Birla Fashion and Retai,527,Marasur Village,Retail Ltd),Anekal Main Road,Anekal Taluk-562106,Karnataka',
                   'Fit:Slim Fit', 'product_uuid:8c3f3dded8de4c4ca2f8535d0249e1b4', 'SubBrand:Allen Solly',
                   'StyleCode:ASBZWSLFD74712', 'Material:60% Polyester, 23% Viscose, 15% Wool and 2% Elastane',
                   'Brand Fit:Slim Fit', 'Weight in gms:300']
    # test_fabric(db, ontology, tags)
    # test_non_image(db, ontology, tags, vendor_tags)
    # material = '64% Cotton, 33% Nylon,3% Spandex'
    # material = '65% Polyester and 35 % Viscose'
    material = '95% Cotton 5% Spandex, 220 Gsm'
    # material = '100% Cotto N Pique,220Gsm'
    print(parse_material(material))

    # print(split_word('220Gsm'))
