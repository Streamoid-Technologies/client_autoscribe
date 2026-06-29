import logging

logger = logging.getLogger(__name__)

import json
import requests


def generate_message_card(facts_dict, title, actions):
    facts = [{"name": k, "value": v} for k, v in facts_dict.items()]
    actions = [{"@type": "OpenUri", "name": k, "targets": [{"os": "default", "uri": v}]} for k, v in actions.items()]
    out = {"@type": "MessageCard",
           "@context": "https://schema.org/extensions",
           "summary": title,
           "title": title,
           "sections": [{"facts": facts}],
           "potentialAction": actions}
    return out


def generate_product_card(vendor_name, brand_name, style_code, request_id, last_modified):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,
             "Style Code": str(style_code),
             "Request ID": str(request_id),
             "Timestamp": str(last_modified)}
    title = 'Style code "%s" posted in brand "%s" by vendor "%s"' % (style_code, brand_name, vendor_name)
    url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/products/%s/data' % \
          (vendor_name, brand_name, style_code)
    return generate_message_card(facts, title, {'View product data': url})

def generate_product_card_v2(vendor_name, brand_name, style_codes):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,}
    title = 'Style code posted in brand "%s" by vendor "%s"' % (brand_name, vendor_name)
    url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/products/%s/multiple-data' % \
          (vendor_name, brand_name, style_codes)
    return generate_message_card(facts, title, {'View Stylecodes and product data': url})


def generate_curated_card(vendor_name, brand_name, style_code, request_id):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,
             "Style Code": str(style_code),
             "Request ID": str(request_id)}
    title = 'Curated data for style code "%s" posted to brand "%s" of vendor "%s"' % \
            (style_code, brand_name, vendor_name)
    url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/products/%s/view' % \
          (vendor_name, brand_name, style_code)
    return generate_message_card(facts, title, {'View curated data': url})

def generate_final_curated_card(vendor_name, brand_name, pushed_style_codes, failed_style_codes, request_id):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,
             "Length of psuhed Style Codes": len(pushed_style_codes),
             "Failed Push Style Codes": len(failed_style_codes),
             "Request ID": str(request_id)}
    title = 'Curated data for "%s" style code posted to brand "%s" of vendor "%s"' % \
            (str(len(pushed_style_codes)), brand_name, vendor_name)
    curated_url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/export-product-status?style_codes=%s' % \
          (vendor_name, brand_name, ",".join(pushed_style_codes))
    if len(failed_style_codes)>0:
        failed_url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/export-product-status?style_codes=%s' % \
            (vendor_name, brand_name, ",".join(failed_style_codes))
        return generate_message_card(facts, title, {'View curated data': curated_url, 'View Failed data': failed_url})
    else:
        return generate_message_card(facts, title, {'View curated data': curated_url})

def generate_curated_card_catalogix(vendor_name, brand_name, style_code, request_id):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,
             "Style Code": str(style_code),
             "Request ID": str(request_id)}
    title = '(Catalogix) Curated data for style code "%s" posted to brand "%s" of vendor "%s"' % \
            (style_code, brand_name, vendor_name)
    url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/products/%s/catalogix-view' % \
          (vendor_name, brand_name, style_code)
    return generate_message_card(facts, title, {'View curated data': url})

def generate_final_curated_card_catalogix(vendor_name, brand_name, pushed_style_codes, failed_style_codes, request_id):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,
             "Length of pushed Style Codes": len(pushed_style_codes),
             "Failed Push Style Codes": len(failed_style_codes),
             "Request ID": str(request_id)}
    title = '(Catalogix) Curated data for "%s" style code posted to brand "%s" of vendor "%s"' % \
            (str(len(pushed_style_codes)), brand_name, vendor_name)
    curated_url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/export-product-status-catalogix?style_codes=%s' % \
          (vendor_name, brand_name, ",".join(pushed_style_codes))
    if len(failed_style_codes)>0:
        failed_url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/export-product-status-catalogix?style_codes=%s' % \
            (vendor_name, brand_name, ",".join(failed_style_codes))
        return generate_message_card(facts, title, {'View curated data': curated_url, 'View failed data': failed_url})
    else:
        return generate_message_card(facts, title, {'View curated data': curated_url})

def generate_mark_pushed_card(vendor_name, brand_name, style_codes):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,
             "Length of Style Code": len(style_codes)}
    title = 'Marked pushed for Style codes: "%s" in brand "%s" by vendor "%s"' % (",".join(style_codes), brand_name, vendor_name)
    curated_url = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/export-product-status?style_codes=%s' % \
          (vendor_name, brand_name, ",".join(style_codes))
    return generate_message_card(facts, title, {'View marked pushed data': curated_url})


def generate_reject_card(vendor_name, brand_name, style_code):
    facts = {"Vendor Name": vendor_name,
             "Brand Name": brand_name,
             "Style Code": str(style_code)}
    title = 'Style code "%s" data rejected in brand "%s" by vendor "%s"' % (style_code, brand_name, vendor_name)
    url1 = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/products/%s/view' % \
           (vendor_name, brand_name, style_code)
    url2 = 'https://cataloging.streamoid.com/api/autoscribe/vendors/%s/brands/%s/products/%s/rejects' % \
           (vendor_name, brand_name, style_code)
    return generate_message_card(facts, title, {'View curated data': url1, 'View reject comments': url2})


def post_to_teams(connector_url, card_data):
    headers = {'Content-type': 'application/json'}
    r = requests.post(connector_url, headers=headers, data=json.dumps(card_data), timeout=60)
    if r.ok:
        logger.critical(r.text)
    else:
        logger.error('%s %s', r.status_code, r.text)
