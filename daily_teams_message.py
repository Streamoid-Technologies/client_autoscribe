from client_autoscribe_db_v2 import ClientAutoscribeDB
from teams_integration import generate_product_card_v2, post_to_teams
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)


def get_daily_stylecodes(db):
    current_time = datetime.now()
    time_12_hours_ago = current_time - timedelta(hours=6)
    vendors = db.list_vendors()
    for vendor in vendors:
        brands = db.list_brands(vendor)
        
        products_data = db.get_product_data_timestamp(
            vendor, time_12_hours_ago.strftime("%Y-%m-%d %H:%M:%S"), current_time.strftime("%Y-%m-%d %H:%M:%S")
        )
        teams_url = db.teams_urls.get(vendor, {}).get("post", None)
        for brand in brands:
            if brand in products_data.keys() and len(products_data[brand]) > 0:
                logger.info(f"For vendor {vendor}, Brand: {brand}")
                style_codes = ",".join(products_data[brand].keys())
                card = generate_product_card_v2(vendor, brand, style_codes)
                print(card)
                try:
                    post_to_teams(teams_url, card)
                    # logger.info("Not posting to teams for now")
                except Exception as e:
                    logger.exception(e)



if __name__ == "__main__":
    db = ClientAutoscribeDB("localhost")
    get_daily_stylecodes(db)
