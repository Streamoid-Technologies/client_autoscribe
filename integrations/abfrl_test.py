import os
import sys
import logging

logger = logging.getLogger(__name__)

import json
import requests
import pandas as pd
from datetime import datetime
# from pattern.en import singularize
from io import BytesIO

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB
from client_autoscribe.rule_parser import parse_condition, simplify, matches


def fix_season_f21_prev(out):
    if out['Brand'].lower().replace(' ', '') == 'forever21':

        key, val = [(key, val) for key, val in out.items() if key.startswith("Season|")][0]
        season_string = key + val
        season_split = season_string.split('*')
        season_fixed = {x.split('|')[0]: x.split('|')[1] for x in season_split if x != ''}

        season_old = out.pop(key)  # Remove combined info
        logger.info(f'f21 season input: {key}: {val}')
        for key, val in season_fixed.items():
            if key not in out:
                out[key] = val
    return out


def fix_season_f21(out):
    if out.get('Brand', '').lower().replace(' ', '') == 'forever21' and 'Season' not in out.keys():

        key, val = [(key, val) for key, val in out.items() if key.startswith("Season|")][0]
        values = ''.join([key for key, val in out.items() if val == None or val == ''])  # Model size etc.

        season_string = key + val + values
        # print('STRING:', season_string)
        logger.info(f'fix_season:combined_string -{season_string}')
        season_string = season_string.replace('&#039', "'").replace('&quot', '"')  # Remove " and '
        season_string = season_string.replace('Material|100% cotton- Hand wash cold', 'Material|100% Cotton')

        season_split = season_string.split('*')
        season_fixed = {x.split('|')[0]: x.split('|')[1] for x in season_split if x != ''}

        season_old = out.pop(key)  # Remove combined info

        for key, val in season_fixed.items():
            if key not in out:
                out[key] = val

        out['Material'] = out['Material'].replace(',', 'and ').replace('Material-', '')
        out['Brand'] = 'FOREVER 21'  # Brand name format
        out = {k: v for k, v in out.items() if v is not None}  # Remove None valued items
        logger.info(f'fix_season: fixed_dict -{out}')
    return out


def export_as_excel(df):
    excel_file = BytesIO()
    with pd.ExcelWriter(excel_file) as writer:
        for sheet, df1 in df.groupby('target_attributes'):
            df1.drop('target_attributes', axis=1, inplace=True)
            df1.to_excel(writer, sheet_name=sheet, index=False)
    return excel_file.getvalue()


class VendorAdapter(BaseAdapter):
    def __init__(self, vendor_config, brand_config):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)
        this_dir = os.path.dirname(__file__)
        # this_file = os.path.join(this_dir, 'Opening lines & Style tip (New Website).xlsx')
        this_file = os.path.join(this_dir, 'pantaloons_descriptions.xlsx')
        logger.info(this_file)
        self.the_collective_product_type = os.path.join(this_dir, 'the_collective_product_type_logic.csv')
        self.title_desc = self.parse_pantaloons_desc(this_file)
        # logger.info(self.title_desc)
        self.db = ClientAutoscribeDB('localhost')

    def parse_pantaloons_desc(self, file_path):
        df = pd.read_excel(file_path, keep_default_na=False)
        return [row for _, row in df.iterrows()]

    def parse_pantaloons(self, file_path):
        df = pd.read_excel(file_path, keep_default_na=False)
        current_world = None
        current_brand = None
        out = []
        for _, row in df.iterrows():
            #     print(row)
            world = row['WORLD'].strip()
            if len(world) < 1:
                world = current_world
            else:
                current_world = world

            brand = row['BRAND'].strip()
            if len(brand) < 1:
                brand = current_brand
            else:
                current_brand = brand

            product_type = row['PRODUCT TYPE'].strip()
            category = row['Category'].strip()
            condition = row['Condition'].strip()
            if len(product_type) > 0:
                out.append({'World': world.strip() if world is not None else None,
                            'Brand': brand.strip() if brand is not None else None,
                            'Product Type': product_type.strip() if len(product_type) > 0 else None,
                            'Category': category.strip() if len(category) > 0 else None,
                            'Condition': condition if len(condition) > 0 else None,
                            # 'Condition': simplify(parse_condition(condition)) if len(condition) > 0 else [],
                            'OpeningLine': row['OPENING LINE'].strip(), 'StyleTip': row['STYLE TIP'].strip()})
            else:
                current_brand = None

            # if brand is None:
            #     current_world = None
        return out

    def convert_the_collective_output(self, vendor_name, brand_name, style_code, product, output, data):
        # https://streamoid1-my.sharepoint.com/:x:/g/personal/arshiya_streamoid_com/EeCOHWQVZolOv7uc33k0busBQQs3MCG3lt4cnmM2wAr3aQ?e=AcJG4X
        attr_map = {"Weave": "Weave",
                    "Sleeves": "Sleeve Length",
                    "Size Group": "Size Group",
                    "Pattern": "Pattern",
                    "Pack Of": "Pack Of",
                    "Neck": "Neckline",
                    "Length": "Length",
                    "Primary Color": "Color",
                    "Fit": "Style"
                    }
        prod_attrs = ['Option Code/VAN', 'Subbrand', 'EanNumber', 'Size/Brand size',
                      'TC.in Size/Standard size', 'Mrp', 'HSN Number','Gender',
                      'Category', 'PRODUCT CARE', 'Fabric details', 'Features',
                      'NAME', 'Long Description', 'Manufacturer info',
                      'Common and generic name of Product / Commodity', 'Net quantity',
                      'Month and Year of Manufacture', 'Country of Origin / Manufacture / Assembly',
                      'measurements', 'Image links', 'Fabric', 'ProductTitle', 'The Detail', 'WashCare', 'Wash Care']

        if data.get('Color', None):
            data['Color'] = data['Color'].title()

        if output['brand_attributes'].get('Color', None):
            output['brand_attributes']['Color'] = output['brand_attributes']['Color'].title()

        for attr in attr_map:
            mapped_attr = attr_map[attr]
            if mapped_attr in data:
                output['brand_attributes'][attr] = data[mapped_attr]

        for attr in prod_attrs:
            if attr in product:
                output['brand_attributes'][attr] = product[attr]

        output['brand_attributes']['Color Product Naming'] = data.get('Color', None)

        # Add Product Type to brand attributes based on conditions
        df = pd.read_csv(self.the_collective_product_type, dtype=str)
        for _, row in df.iterrows():
            product_type = row['product_type']
            attribute_conditions = row['condition']
            if ':' in attribute_conditions:
                conditions = [cond.split(':') for cond in attribute_conditions.split(',')]
                conditions = [list(map(str.strip, x)) for x in conditions]  # Trim spaces
                match = all(
                    data.get(k.strip('!'), None) != v if k.startswith('!') else data.get(k, None) == v for k, v in
                    conditions)
                if match:
                    output['brand_attributes']['Product Type'] = product_type

        # Modify Sleeves in brand attributes based on conditions
        sleeves = output['brand_attributes'].get('Sleeves', None)
        if sleeves in ['cap-sleeve', 'short-sleeve', 'half-sleeve', 'elbow']:
            output['brand_attributes']['Sleeves'] = 'Short Sleeves'
        if sleeves in ['three-fourth', 'full-sleeve', 'extended-long', 'magyar']:
            output['brand_attributes']['Sleeves'] = 'Long Sleeves'
        if sleeves in ['sleeveless']:
            output['brand_attributes']['Sleeves'] = 'Sleeveless'

        # Modify Pattern values
        pattern = output['brand_attributes'].get('Pattern', None)
        if pattern in ['horizontal-stripes', 'vertical-stripes']:
            output['brand_attributes']['Pattern'] = 'striped'

        return output

    def convert_output(self, output, **kwargs):
        output = super().convert_output(output)
        if 'RequestID' not in output:
            return output
        if output['RequestID'] is not None:
            output['RequestID'] = str(output['RequestID'])

        '''
        vendor_name = self.vendor_config['_id']
        brand_name = self.brand_config['_id']
        style_code = output['StyleCode']
        product = kwargs.get('product', None)
        data = kwargs.get('data', None)

        if product and data and brand_name == 'the_collective':
            logger.info(f"OUTPUT, {output}")
            logger.info("DATA:, {data}")
            logger.info("PRODUCT:, {product}")
            output = self.convert_the_collective_output(vendor_name, brand_name, style_code, product, output, data)
        '''
        return output

    def get_output_file(self, data, **kwargs):

        df_list = []
        for idx, row in data.items():
            style_code = row.pop('StyleCode')
            request_id = row.pop('RequestID')
            df1 = pd.DataFrame(row).T
            df1 = df1.rename_axis('target_attributes').reset_index()
            df1['RequestID'] = request_id
            df1['StyleCode'] = style_code
            df_list.append(df1)

        out_df = pd.concat(df_list)
        excel = export_as_excel(out_df)
        return excel, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    def convert_input(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        if filename is None:
            return [fix_season_f21(d) for d in data]  # data

        style_codes = []
        if filename.lower().endswith('.csv'):
            data = pd.read_csv(data, keep_default_na=False, dtype=str)
            data.fillna('', inplace=True)
            data = [dict(row) for _, row in data.iterrows()]

            for row in data:
                image_urls = []
                for i in range(1, 11):
                    field = 'id_image%d' % i
                    if field not in row:
                        break
                    val = str(row[field]).strip()
                    if val.startswith('http'):
                        image_urls.append(val)
                    row.pop(field)
                logger.info(dict(row))
                out = {key.strip(): val.strip() for key, val in row.items()}
                out['ImageURLs'] = ','.join(image_urls)
                out['StyleCode'] = out.pop('Style code')
                out['RequestID'] = 'A' + str(row['PID'])
                out = fix_season_f21(out)
                logger.info(out)
                style_codes.append(out)

        return style_codes

    def post_to_vendor(self, output, **kwargs):
        # https://db.tools.streamoid.com/feed/insecure/get_data/?path=/autoscribe/v_peter_england_autoscribe_2020_06_01_08_22_16.json
        brand_name = self.brand_config['_id']
        vendor_name = self.vendor_config['_id']
        # Trigger unknown!
        url = "https://jnrmmg2h5m.execute-api.ap-southeast-1.amazonaws.com/testing/scribe"

        # reviewed in cataloging
        path = datetime.utcnow().strftime('%Y/%m/%d/%H/%M/%S')
        # TODO: push API key to vendor config
        headers = {'x-api-key': 'U6BX0yOhtP6JBXOaPwkRm5LqmVR1soqW4k9rJmv0'}
        style_code = output['StyleCode']
        data = json.dumps([output])
        requestid = output['RequestID']
        ProductID = output.get('ProductID', None)
        if requestid is None:
            logger.warning('StyleCode %s has no RequestID', style_code)
            return False

        params = {'requestid': requestid, 'ProductID':ProductID, 'type': 'update', 'path': path}
        logger.info(url)
        logger.info(params)
        logger.info(data)
        r = requests.post(url, params=params, data=data, headers=headers, timeout=60)

        if r.ok:
            res = r.text
            logger.info('%s %s', style_code, res)
            logger.info(f"VENDOR: abfrl_test, TIME_STAMP: {path}, STYLE_CODE: {style_code}, RESPONSE: {res}")
            self.db.save_pushed_details(vendor_name, brand_name, style_code, requestid, res)
            return True
            # self.on_push(vendor_name, brand_name, style_code, requestid)
        else:
            res = f'{r.status_code} {r.text}'
            self.db.save_pushed_details(vendor_name, brand_name, style_code, requestid, res)
            logger.error('%s %s', r.status_code, r.content)
            return False

    def pantaloons_translations(self, product, curated, translated):
        # title
        logger.info(curated)
        brand = product['SubBrand']
        color = curated['Color']
        title_category = translated.get('Sub Product', '')
        category1 = curated['Category']
        department = curated['Department']
        # category = singularize(category1.lower())
        desc = curated.get('description', '')

        color = color.replace('multi', 'multicoloured')
        title = '%s %s %s' % (brand, color, title_category)
        title = ' '.join([x.capitalize() for x in title.split(' ')])

        # description
        description = ''
        short_desc = ''
        tags = [k + ':' + v for k, v in curated.items()]
        # logger.info(tags)
        for row in self.title_desc:
            # logger.info('%s %s', row['Condition'], matches(tags, row['Condition']))
            condition = parse_condition(row['Condition']) if len(row['Condition']) > 0 else {}
            if row['Category'] == category1 and \
                    row['Brand'].lower() == brand.lower() and \
                    department in [x.strip() for x in row['Department'].lower().split(',')] and \
                    matches(tags, condition):
                short_desc = row['StyleTip']
                description = '%s %s %s' % (row['OpeningLine'], desc, row['StyleTip'])
                break

        return {'Title': title, 'Description': description, 'Short Description': short_desc}

    def myntra_translations(self, product):
        out = {}
        if 'Material' in product and '100% polyurethane' in product['Material'].lower():
            out['Material'] = 'Leather'
        if 'Sole Material' in product and '100% rubber' in product['Sole Material'].lower():
            out['Sole Material'] = 'Rubber'
        return out

    def translate(self, target_ontology, product, curated, translated, **kwargs):
        if self.brand_config['_id'] == 'pantaloons' and target_ontology == 'Pantaloons-MP':
            return self.pantaloons_translations(product, curated, translated)
        elif self.brand_config['_id'] == 'forever21' and target_ontology == 'Myntra-MP':
            return self.myntra_translations(product)

        return {}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    adapter = VendorAdapter(None, None)
    pd.DataFrame(adapter.title_desc).to_csv('pantaloons_descriptions.csv', index=False)
