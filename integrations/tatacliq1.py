import sys
import logging

logger = logging.getLogger(__name__)

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter

from datetime import date
from dateutil.relativedelta import relativedelta


def get_product_title(products, curated, translated):
    # Standard format: 'Brand Name+ Color+ Pattern+ Display Product Name'
    # Get colour, pattern and category from curated and brand from product
    brand = products.get("Brand (Refer LOV List", "")
    color = curated.get('Color', "")
    pattern = curated.get('Pattern', "")
    display = products.get("Display Product Name", "")
    title = f"{brand} {color} {pattern} {display}"

    return title


def remove_special_chars(string, chars=['@', '$', '%']):
    for char in chars:
        string = string.replace(char, "")
    return string


def get_pit_image(seller_code, sku_code, image_urls, max_length=200):
    # Image format – Sellercode_SKUcode_imagenumber.jpeg
    # Additionally need seller code, may need sequence ordering
    num_images = len(image_urls.split(','))
    img_list = [f'{seller_code}_{sku_code}_{num}.jpeg' for num in range(1, num_images + 1)]
    pit_image = ','.join(img_list)

    return pit_image


def get_default_weight(category):
    # 400 only For innerwear and accessory categories
    # category field?
    weight = 450
    return weight


def flag_fabric_missing(sku_code):
    # zero value?
    zero_value = 'NA'
    logger.info(f'Fabric family misssing for: {sku_code}')
    return zero_value


def get_model_fit(gender='men', size='M'):
    # Size: alphabetical, #ATTR_mencasualtopwearsize_Size*
    # Gender: field unclear
    if gender == 'men':
      model_fit = f"""Model is 6'0"/185 cms and is wearing {size}"""
    elif gender == 'women':
      model_fit = f"""Model is 5'5"/165 cms and is wearing {size}"""

    return model_fit


class VendorAdapter(BaseAdapter):
    def __init__(self, vendor_config, brand_config):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)

    def convert_input(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        if filename is not None and filename.lower().endswith('.csv'):
            data = pd.read_csv(data, keep_default_na=False, dtype=str)
            data.fillna('', inplace=True)
            data = [dict(row) for _, row in data.iterrows()]

        style_codes = []
        for row in data:
            # logger.info(dict(row))
            out = {key.strip(): val.strip() for key, val in row.items()}
            image_urls = []
            for i in range(3):
                field = 'Image URL%d' % (i + 1)
                if field in out:
                    image_urls.append(out[field])
            if len(image_urls) < 1:
                logger.warning('Skipping: No image URLs: %s', out)
                continue
            out['ImageURLs'] = ','.join([x for x in image_urls if len(x) > 0])
            out['StyleCode'] = str(out['Seller Article SKU'])
            out['RequestID'] = str(out['Seller Article SKU'])
            logger.info(out)
            style_codes.append(out)
        return style_codes

    def translate(self, products, curated, translated, **kwargs):

        product_upload_status = 'S'
        hsn_code = products.get("HSN CODE", "")
        sku_code = products.get("Seller Article SKU", "").strip()
        product_title = products.get("PRODUCT TITLE", get_product_title(products, curated))
        product_name = products.get("PRODUCT NAME", product_title)

        # product description
        brand_name = products.get("Brand (Refer LOV List", '')
        desc = products.get("PRODUCT DESCRIPTION", '')
        desc = remove_special_chars(desc)
        product_description = f"{brand_name} {desc}"

        # start and end date
        today = date.today()
        start_date = today.strftime("%d/%m/%Y")
        end_date = (today + relativedelta(years=10)).strftime("%d/%m/%Y")  # format separator '/'

        # pit_image
        image_urls = products.get("ImageURLs", None)
        seller_code = ""  # Should be available in product data
        pit_image = get_pit_image(seller_code, sku_code, image_urls)  # Need verification

        # weight
        category = ''  # Display Product Name will always be the category
        weight = products.get("PRODUCT WEIGHT [gm]", get_default_weigth(category))

        fabric_family = products.get("Fabric Family (Refer LOV List)",
                                     flag_fabric_missing(sku_code))  # We specify zero value and flag
        style_note = "Same as description"
        age_band = products.get("Age Band (Refer LOV List)", "22-35")
        color = translated.get("Color (Refer LOV List),")  # Assume translated LOV
        brand_description = brand_name
        fit = products.get("Model Fit", "")  # Check if it already in existing translation
        seller_product_association_status = "Yes"

        neck_collar = translated.get("Neck/Collar (Refer LOV List)", "")  # Assume translated LOV
        sleeve = translated.get("Sleeve (Refer LOV List)", "")  # Assume translated LOV
        size_chart = translated.get("Size (Refer LOV List)", "")  # Assume translated LOV
        occasion = translated.get("Occasion (Refer LOV List)", "")  # Assume translated LOV

        warranty_type = 'NA'
        lead_type = '1'
        wash = products.get("Wash", "")
        style_code = products.get("Style Code", "")

        # multi-pack ("Yes"/"No")
        multi_pack = translated.get("Multi Pack (Refer LOV List", "")  # Assume translated LOV

        display_product_name = products.get("Display Product Name")  # Missing value by tool?

        mode_fit = get_model_fit(gender, size)  # Need clarity on gender, size field and formats
        color_family = translated.get("Color Family (Refer LOV List)", "")  # Assume translated LOV
        warranty_time_period = 'NA'
        mrp_value = products.get("MRP [INR]", "")

        fabric = translated.get("Fabric", "")  # Assume translated LOV
        size = translated.get("Size (Refer LOV List)", "")  # Assume translated LOV
        dress_length = translated.get("Dress Length (Refer LOV List)", "")  # Assume translated LOV
        image_priority = 1

        # Non mandatory fields
        meta_title = f"Buy {product_title} for Men Online @ Tata CLiQ"
        meta_keyword = f"{product_title}, Buy, Online, India, Tata CLiQ"
        meta_description = f"Shop {product_title} for MenOnline at best price in India at Tata CLiQ. Choose your favourite Western Wear Online & Get free Shipping."
        pbi_identity_code = ""  # Not found in seller, dummy method unclear
        dress_shape = translated.get("Dial Shape (Refer LOV List)", "")  # Assume translated LOV
        pattern = translated, get("Pattern (Refer LOV List)", )  # Assume translated LOV

        brand_attributes = {
            "PRODUCTUPLOADSTATUS*": product_upload_status,
            "HSNCODE*": hsn_code,
            "SKUCODE*": sku_code,
            "TITLE*": product_title,
            "NAME*": product_name,
            "DESCRIPTION*": product_description,
            "STARTDATE*": start_date,
            "ENDDATE*": end_date,
            "PITIMAGE*": pit_image,
            "WEIGHT*": weight,
            "#ATTR_womenfabric_Fabric Family*": fabric_family,
            "#ATTR_stylenote_Style Note*": style_note,
            "#ATTR_ageband_Age Band*": age_band,
            "#ATTR_colorapparel_Color*": color,
            "#ATTR_brandDescription_Brand Description*": brand_description,
            "#ATTR_womentopwearfit_Fit*": fit,
            "#ATTR_brand_Brand*": brand_name,
            "#ATTR_weightapparel_Weight*": weight,
            "#ATTR_sellerAssociationStatus_Seller Product Association Status*": seller_product_association_status,
            "#ATTR_womencasualdressjumperneckcollar_Neck/Collar*": neck_collar,
            "#ATTR_womencasualdressjumpersleeve_Sleeve*": sleeve,
            "#ATTR_sizechart_Size Chart*": size_chart,
            "#ATTR_warrantyType_Warranty Type*": warranty_type,
            "#ATTR_leadTimeForTheSKUHomeDelivery_Lead time for the SKU - Home Delivery [No. of Minute]*": lead_type,
            "#ATTR_washcare_Wash*": wash,
            "#ATTR_stylecode_Style Code*": style_code,
            "#ATTR_occasion_Occasion*": occasion,
            "#ATTR_multipack_Multi Pack*": multi_pack,
            "#ATTR_displayproduct_Display Product Name*": display_product_name,
            "#ATTR_modelfit_Model Fit* ": mode_fit,
            "#ATTR_colorfamilyapparel_Color Family*": color_family,
            "#ATTR_warrantyTimePeriod_Warranty Time Period [Months]*": warranty_time_period,
            "#ATTR_mrp_MRP [INR]*": mrp_value,
            "#ATTR_fabricapparel_Fabric*": fabric,
            "#ATTR_womencasualweardressesjumperssize_Size*": size,
            "#ATTR_dresslength_Dress Length*": dress_length,
            "IMAGEPRIORITY": image_priority,
            "METATITLE": meta_title,  # NON-MANDATORY
            "METAKEYWORD": meta_keyword,  # NON-MANDATORY
            "METADESCRIPTION": meta_description,  # NON-MANDATORY
            "PBIIDENTITYVALUE": pbi_identity_code,  # NON-MANDATORY
            "#ATTR_leadvariantid_Lead Variant ID": sku_code,  # NON-MANDATORY
            "#ATTR_womenpattern_Pattern": pattern,  # NON-MANDATORY
            "#ATTR_dressshape_Dress Shape": dress_shape,  # NON-MANDATORY
        }

        output = {"brand_attributes": brand_attributes}

        return output

