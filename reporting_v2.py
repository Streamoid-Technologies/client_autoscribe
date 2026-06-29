import os
import sys
import logging

logger = logging.getLogger(__name__)

import re
import pandas as pd
from datetime import datetime, timedelta
from pymongo import ASCENDING
from pprint import pprint

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.ontology_db import unescape
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB, get_db_name


def compute_hours(date1, date2):
    if date2 is None:
        return None
    d1 = datetime.strptime(date1, '%Y-%m-%d %H:%M:%S')
    d2 = datetime.strptime(date2, '%Y-%m-%d %H:%M:%S')
    dt = d2 - d1
    return dt.days * 24 + dt.seconds / 3600


def get_dates(start_date, end_date):
    date1 = datetime.strptime(start_date, '%Y-%m-%d').date()
    date2 = datetime.strptime(end_date, '%Y-%m-%d').date()
    td = timedelta(days=1)
    if date1 > date2:
        date1, date2 = date2, date1
    date = date1
    dates = []
    while date <= date2:
        dates.append(date.strftime('%Y-%m-%d'))
        date += td
    return dates


class ClientAutoscribeReporting(ClientAutoscribeDB):
    def __init__(self, mongo_host):
        super().__init__(mongo_host)

        # ontology - field name - channel name
        self.channels = {'Ajio': "ajio_attributes",
                         'Amazon': "amazon_attributes",
                         'Flipkart': "flipkart_attributes",
                         'Myntra': "myntra_attributes",
                         'Limeroad': "limeroad_attributes",
                         'Paytm': "paytm_attributes",
                         'TataCliq': "tatacliq_attributes",
                         'Nykaa': "nykaa_attributes"}

    def style_codes_pushed(self, vendor_name, brand_name, start_date, end_date):
        vendor_db = get_db_name(vendor_name)
        products_coll = self.products_coll_pattern % brand_name
        coll = self.client[vendor_db][products_coll]
        dates = get_dates(start_date, end_date)
        out = {}
        for date in dates:
            for row in coll.find({'last_modified': re.compile('^%s' % date)}):
                out[row['_id']] = unescape(row)
        return out

    def style_codes_processed(self, vendor_name, brand_name, start_date, end_date):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]
        dates = get_dates(start_date, end_date)
        out = {}
        for date in dates:
            for row in coll.find({'push_time': re.compile('^%s' % date)}):
                out[row['_id']] = row
        return out

    def style_codes_pushed_and_processed(self, vendor_name, brand_name, start_date, end_date):
        return set(self.style_codes_pushed(vendor_name, brand_name, start_date, end_date)) & \
            set(self.style_codes_processed(vendor_name, brand_name, start_date, end_date))

    def analyse_tat(self, vendor_name, brand_name, start_date, end_date):
        style_codes = self.style_codes_processed(vendor_name, brand_name, start_date, end_date)
        logger.info('%s %d', vendor_name, len(style_codes))
        vendor_db = get_db_name(vendor_name)
        products_coll = self.products_coll_pattern % brand_name
        coll = self.client[vendor_db][products_coll]
        out = {}
        for style_code, details in style_codes.items():
            for row in coll.find({'_id': style_code}):
                push_time = details.get('push_time', None)
                if push_time is None:
                    continue
                out[style_code] = {'submit_time': row['last_modified']}
                out[style_code]['push_time'] = push_time
                tat = compute_hours(row['last_modified'], push_time)
                logger.info('%s %s %s %f', style_code, row['last_modified'], push_time, tat)
                out[style_code]['tat_in_hours'] = tat

        return out

    def accuracy(self, vendor_name, brand_name, start_date, end_date):
        out = {}
        out1 = self.range_accuracy(vendor_name, brand_name, start_date, end_date)
        for k, v in out1.items():
            if k not in out:
                out[k] = {'wrong': 0, 'total': 0}
            out[k]['wrong'] += v['wrong']
            out[k]['total'] += v['total']

        return {k: 1 - v['wrong'] / (v['total'] + 0.0001) for k, v in out.items()}

    def range_accuracy(self, vendor_name, brand_name, start_date, end_date, style_codes=None):
        # - by streamoid category
        if style_codes is None:
            style_codes = self.style_codes_pushed(vendor_name, brand_name, start_date, end_date)
        out = {}
        for style_code in style_codes:
            category, wrong, total = self.get_product_data(vendor_name, brand_name, style_code)
            if category is None:
                continue
            if category not in out:
                out[category] = {'Style Codes': {'total': 0, 'wrong': 0}}
            out[category]['Style Codes']['total'] += 1
            # wrong1 = False
            wrong2 = {self.get_attribute_field(k): v for k, v in wrong.items()}
            for field, count in total.items():
                if field not in out[category]:
                    out[category][field] = {'wrong': 0, 'total': 0}
                out[category][field]['total'] += count
                out[category][field]['wrong'] += len(wrong2[field]) if field in wrong2 else 0
                # if out[category][field]['wrong'] > 0:
                #     wrong1 = True
            # if wrong1:
            #     out[category]['Style Codes']['wrong'] += 1

        return out

    def top_rejected_attributes(self, vendor_name, brand_name, start_date, end_date):
        out = {}
        out1, _ = self.range_rejected_attributes(vendor_name, brand_name, start_date, end_date)
        for field, counts in out1.items():
            if field not in out:
                out[field] = {}
            for k, v in counts.items():
                if k not in out[field]:
                    out[field][k] = 0
                out[field][k] += v
        return out

    def range_rejected_attributes(self, vendor_name, brand_name,
                                  start_date, end_date, style_codes=None):
        # - by marketplace
        if style_codes is None:
            style_codes = self.style_codes_pushed(vendor_name, brand_name,
                                                  start_date, end_date)
        out = {}
        raw = []
        for style_code in style_codes:
            category, wrong, total = self.get_product_data(vendor_name,
                                                           brand_name, style_code)
            # field is channel here
            for field, vals in wrong.items():
                if field not in out:
                    out[field] = {}
                for k in vals:
                    if k not in out[field]:
                        out[field][k] = 0
                    out[field][k] += 1
                    raw.append({'Brand': vendor_name, 'Style Code': style_code,
                                'Marketplace': field, 'MP Attribute Name': k, 'Category': category})
        return out, raw

    def get_rejected_product(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        rejects_coll = self.rejects_coll_pattern % brand_name
        coll = self.client[vendor_db][rejects_coll]
        for row in coll.find({'StyleCode': style_code}).sort('submit_time', ASCENDING):
            return row

    def get_cataloging_product(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]
        for row in coll.find({'_id': style_code}):
            return row

    def get_product_data(self, vendor_name, brand_name, style_code):
        data = self.get_output(vendor_name, brand_name, style_code)
        if data is None:
            return None, {}, {}

        # total -> field -> value
        total = {}
        for k, v in data.items():
            if isinstance(v, dict):
                total[k] = len(v)

        rejections = self.get_rejected_product(vendor_name, brand_name, style_code)
        # wrong -> field -> key -> value
        wrong = {}
        if rejections is not None:
            for row in rejections['Rejects']:
                channel = row['Channel']
                field = self.get_attribute_field(channel)
                if row['Remarks'] is None:
                    continue
                remarks = row['Remarks'].strip().split(',')
                logger.info(remarks)
                wrong[channel] = {}
                for rem in remarks:
                    words = rem.split(':')
                    if len(words) != 2:
                        logger.warning(words)
                        continue
                    k = words[0].strip()
                    v = words[1].strip()
                    if k in data[field]:  # and data[field][k] != v:
                        wrong[channel][k] = v

        data1 = self.get_cataloging_product(vendor_name, brand_name, style_code)
        # print(data1['data'])
        category = data1['data']['Category']
        return category, wrong, total

    def get_attribute_field(self, channel):
        field = self.channels.get(channel, None)
        if field is None:
            return self.brand_target
        else:
            return field

    def get_rejection_channels(self, vendor_name, brand_name):
        vendor_db = get_db_name(vendor_name)
        rejects_coll = self.rejects_coll_pattern % brand_name
        coll = self.client[vendor_db][rejects_coll]
        channels = {}
        for row in coll.find():
            for row1 in row['Rejects']:
                channel = row1['Channel']
                if channel not in channels:
                    channels[channel] = 0
                channels[channel] += 1
        return channels

    def get_report_data(self, vendor_name, brand_name, start_date, end_date):
        out = {}
        # Volume report
        processed = list(self.style_codes_processed(vendor_name, brand_name, start_date, end_date).keys())
        logger.info('%s %s', vendor_name, processed)
        out['pushed'] = len(self.style_codes_pushed(vendor_name, brand_name, start_date, end_date))
        out['processed'] = len(processed)
        out['pushed_and_processed'] = len(self.style_codes_pushed_and_processed(
            vendor_name, brand_name, start_date, end_date))

        # TAT
        tat = {'within_6': 0, 'within_24': 0, 'within_48': 0, 'over_48': 0}
        values = self.analyse_tat(vendor_name, brand_name, start_date, end_date).values()
        for val in values:
            hours = val['tat_in_hours']
            if hours <= 6:
                tat['within_6'] += 1
            elif hours <= 24:
                tat['within_24'] += 1
            elif hours <= 48:
                tat['within_48'] += 1
            else:
                tat['over_48'] += 1
        out['tat'] = tat
        out['tat_total'] = len(values)

        # accuracy
        out['accuracy'] = self.range_accuracy(vendor_name, brand_name,
                                              start_date, end_date, processed)

        # rejected
        out['rejected'], out['rejected_raw'] = self.range_rejected_attributes(
            vendor_name, brand_name, start_date, end_date, processed)
        return out

    def _write_tat_row1(self, f, msg, field, vendors, data):
        f.write('% Style Codes with TAT ' + msg)
        for v in vendors:
            f.write(',%.1f' % (100 * data[v]['tat'][field] / (data[v]['tat_total'] + 0.0001)))
        f.write('\n')

    def _write_tat_row2(self, f, msg, field, vendors, data):
        f.write('No. of Style Codes with TAT ' + msg)
        for v in vendors:
            f.write(',%d' % data[v]['tat'][field])
        f.write('\n')

    def _write_acc_row(self, f, data, forder):
        for vendor, data1 in data.items():
            accuracy = data1['accuracy']
            if len(accuracy) < 1:
                continue
            f.write(vendor + '\n')
            for category, fields in accuracy.items():
                if category is None:
                    print(vendor)
                    pprint(accuracy)
                f.write(',' + category + ',' + 'Accuracy')
                for field in forder:
                    d1 = fields.get(field, {'wrong': 0, 'total': 0})
                    if field == 'Style Codes':
                        f.write(',')
                    else:
                        f.write(',%.1f%%' % (100 * (1 - d1['wrong'] / (d1['total'] + 0.00001))))
                f.write('\n')
                f.write(',,' + 'Support')
                for field in forder:
                    d1 = fields.get(field, {'wrong': 0, 'total': 0})
                    f.write(',%d' % d1['total'])
                f.write('\n')
                f.write(',,' + 'Rejects')
                for field in forder:
                    d1 = fields.get(field, {'wrong': 0, 'total': 0})
                    f.write(',%d' % d1['wrong'])
                f.write('\n')
            f.write('\n')

    def _write_rej_row(self, f, data, key_order):
        for vendor, data1 in data.items():
            rejected = data1['rejected']
            if len(rejected) < 1:
                continue
            pprint(rejected)
            f.write(vendor + '\n')
            for field, keys in rejected.items():
                f.write(',' + field)
                for key in key_order:
                    f.write(',%d' % keys.get(key, 0))
                f.write('\n')
            f.write('\n')

    def create_report(self, vendor_name, start_date, end_date, f):
        brands = self.get_brand_names(vendor_name)
        data = {}
        for brand_name in brands:
            data[brand_name] = self.get_report_data(vendor_name, brand_name,
                                                    start_date, end_date)

        f.write('Volume Report\n\n')
        f.write(',' + ','.join(brands) + '\n')
        f.write('Style Codes Pushed,')
        f.write(','.join([str(data[v]['pushed']) for v in brands]) + '\n')
        f.write('Style Codes Processed,')
        f.write(','.join([str(data[v]['processed']) for v in brands]) + '\n')
        f.write('Overlap of Style Codes Pushed & Processed,')
        f.write(','.join([str(data[v]['pushed_and_processed']) for v in brands]) + '\n')
        f.write('\n')

        f.write('Turn Around Time\n\n')
        f.write(',' + ','.join(brands) + '\n')

        for msg, field in [('in 6 hours', 'within_6'),
                           ('in 24 hours', 'within_24'),
                           ('in 48 hours', 'within_48'),
                           ('more than 48 hours', 'over_48')]:
            self._write_tat_row1(f, msg, field, brands, data)
            self._write_tat_row2(f, msg, field, brands, data)
        f.write('\n')

        forder = list(set([f1 for v in data for fs in data[v]['accuracy'].values() for f1 in fs]))
        logger.info(data)
        logger.info(forder)
        try:
            forder.remove('Style Codes')
            forder.append('Style Codes')
        except:
            pass
        forder2 = [x.replace('_attributes', '').capitalize() for x in forder]
        f.write('Accuracy View,,,' + ','.join(forder2) + '\n\n')
        self._write_acc_row(f, data, forder)

        logger.info(data)
        key_order = list(set([key for v in data for keys in data[v]['rejected'].values() for key in keys]))
        logger.info(key_order)
        f.write('Rejection View,,' + ','.join(key_order) + '\n\n')
        self._write_rej_row(f, data, key_order)

        f.write('Raw data for rejects\n\n')
        header = ['Brand', 'Style Code', 'Marketplace', 'MP Attribute Name', 'Category']
        f.write(','.join(header) + '\n')
        for v in brands:
            raw = data[v]['rejected_raw']
            for raw1 in raw:
                f.write(','.join([raw1[f] for f in header]) + '\n')

    def get_monthly_received(self, vendor_name, start_date, end_date, f):
        vendor_db = get_db_name(vendor_name)
        brands = self.list_brands(vendor_name)
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        results = []
        for brand_name in brands:
            products_coll = self.products_coll_pattern % brand_name
            coll = self.client[vendor_db][products_coll]

            for item in coll.find({}):
                received_time = item.get('last_modified', None)
                style_code = item.get('_id', None)
                if received_time and style_code:
                    this_date = datetime.strptime(received_time, '%Y-%m-%d %H:%M:%S').date()
                    if start_date <= this_date <= end_date:
                        date = this_date.strftime("%b-%Y")
                        results.append(
                            {'stylecode': style_code, 'brand': brand_name, 'date': date, 'timestamp': received_time})
        df = pd.DataFrame(results)
        with pd.ExcelWriter(f) as writer:
            if( results == []):
                df.to_excel(writer, sheet_name='full')
            else:
                for date, df1 in df.groupby('date'):
                    df2 = df1['brand'].value_counts().rename_axis('brand').to_frame('counts')
                    df2.to_excel(writer, sheet_name=str(date))
                df.to_excel(writer, sheet_name='full')

    def get_brand_names(self, vendor_name):
        brands = self.list_brands(vendor_name)
        # TODO: configure this within brands config
        return list(set(brands) - {'forever21', 'abof', 'people', 'pantaloons', 'skult'})


if __name__ == '__main__':
    from pprint import pprint

    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    start_date = sys.argv[1]
    end_date = sys.argv[2]
    rep = ClientAutoscribeReporting('localhost')
    # vendor_names = rep.get_vendor_names2()
    # print(vendor_names)
    # vendor_names = ['allen_solly', 'louis_philippe', 'vanheusen', 'peter_england']
    # vendor_names = ['louis_philippe']
    vendor_name = 'abfrl_lbrd_prod'
    with open('report_%s_%s.csv' % (start_date, end_date), 'w') as f:
        rep.create_report(vendor_name, start_date, end_date, f)
