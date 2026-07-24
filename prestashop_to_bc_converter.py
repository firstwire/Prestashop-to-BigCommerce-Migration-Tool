"""
PrestaShop -> BigCommerce CSV Converter
=========================================
Converts a PrestaShop database CSV export into BigCommerce-ready import
files:

    bigcommerce_products.csv              -> BigCommerce Admin > Products > Import
    bigcommerce_customers.csv             -> BigCommerce Admin > Customers > Import
    bigcommerce_addresses_bulk_import.csv -> companion file, see note below
    bigcommerce_orders_bulk_import.csv    -> companion file, see note below

No API credentials required -- just drop your PrestaShop table exports
(one CSV per table, named like the table, e.g. ps_product.csv) into the
input/ folder next to this script and run it.

INPUT TABLES USED
------------------
    ps_product, ps_product_lang            (core product data)
    ps_manufacturer                        (brand/vendor)
    ps_category_lang                       (category names)
    ps_attribute, ps_attribute_group        (variant option names/values)
    ps_product_attribute                   (variant/combination rows)
    ps_product_attribute_combination       (links a combination to its attributes)
    ps_stock_available                     (inventory quantity)
    ps_image, ps_image_lang                (product images + alt text)
    ps_tag                                 (tags, IF it has an id_product column
                                             -- see NOTE 1 below)
    ps_feature_product                     (feature/attribute id links -- see NOTE 2)
    ps_customer                            (customer records)
    ps_address                             (customer addresses)
    ps_orders                              (order header data -- see NOTE 3)

REQUIREMENTS
------------
    pip install pandas --break-system-packages

USAGE
-----
    python prestashop_to_bc_converter.py

============================================================================
NOTE 1 -- ps_tag has no ps_product_tag table alongside it in this export.
PrestaShop normally links tags to products through ps_product_tag
(id_product, id_tag, id_lang). Without that table there is no reliable way
to know which tag belongs to which product. The script checks: if your
ps_tag.csv happens to already contain an id_product column, tags import
normally. Otherwise it logs a warning and leaves Search Keywords blank for
tags (Category-derived keywords still populate). Export ps_product_tag.csv
from PrestaShop and re-run if you need tags.

NOTE 2 -- ps_feature_product only maps id_product -> id_feature -> id_value.
Without ps_feature (feature names) and ps_feature_value_lang (value text),
the script cannot resolve human-readable feature names, so features are
skipped. Export those two tables if you want features carried over as
BigCommerce custom fields.

NOTE 3 -- Orders now map to BigCommerce's own native Order export CSV
layout column-for-column (Order ID, Customer ID, Billing Street 1,
Product Details, etc.) rather than a generic guessed layout. Some columns
still need optional PrestaShop tables that weren't in the original table
list to fully populate:
    ps_order_detail       -> Product Details (line items) + Total Quantity
    ps_state               -> Billing/Shipping State as a real code, not a raw id
    ps_country_lang        -> Billing/Shipping Country as a real name, not a raw id
    ps_currency            -> Order Currency Code as an ISO code, not a raw id
Without these, the corresponding columns fall back to blank/0 or the raw
PrestaShop internal ID, and a warning is logged. Everything else (order
header, billing/shipping name+address+phone, totals, payment method,
status) is populated from ps_orders + ps_customer + ps_address as before.

============================================================================
IMPORTANT -- CONFIRMED BIGCOMMERCE IMPORT BEHAVIOR (from prior live testing
on a real BigCommerce store migrating from Shopify/OpenCart)
============================================================================
  * BigCommerce's Customer CSV importer REJECTS every row that includes
    address data, regardless of country format (ISO-2 code or full name).
    So bigcommerce_customers.csv intentionally has NO address columns.
    Addresses go to a separate bigcommerce_addresses_bulk_import.csv,
    shaped for the Matrixify app's Customer import (BigCommerce Admin ->
    Apps -> Matrixify -> Import -> Customers), which does support them.

  * BigCommerce's Product CSV importer does NOT accept guessed values for
    a "Track Inventory" or "Product URL" column -- two different guesses
    for each ("Y"/"N", "product"/"none", blank/generated URL) all failed
    on real imports. Both columns are deliberately left OUT of
    bigcommerce_products.csv; BigCommerce applies its own inventory
    tracking default and auto-generates the product URL from the name.

  * BigCommerce has no native CSV import for Orders at all. Order data is
    written to a generic, clearly-labelled bigcommerce_orders_bulk_import.csv
    intended for whichever import app you use from BigCommerce's
    Data Transfer / Migration Services category (most of these apps let
    you manually map columns during import) -- this format is NOT
    verified against any single specific app's template.

  * Variant/combination rows are written as separate "SKU" item-type rows
    under the parent "Product" row (Product Code/SKU, Price, Retail Price,
    Weight only) rather than attempting to push full option name/value
    pairs through this basic CSV -- BigCommerce's bulk CSV format doesn't
    have clean, confirmed columns for arbitrary option name/value pairs,
    so encoding guessed column names here would risk the same kind of
    silent-failure the Track Inventory guesses caused. If you need full
    swatch/option data with option value assignment, that's a Product
    Options CSV import (a separate BigCommerce feature) built from
    ps_attribute_group / ps_attribute -- ask if you want that added.
============================================================================
"""

import os
import re
import csv
import sys
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, "input")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

# ----------------------------------------------------------------------
# Config -- adjust to match your store
# ----------------------------------------------------------------------
PRESTASHOP_STORE_URL = "https://your-prestashop-store.com"
PRESTASHOP_IMAGE_BASE_URL = f"{PRESTASHOP_STORE_URL.rstrip('/')}/img/p"
DEFAULT_LANGUAGE_ID = "1"
DEFAULT_WEIGHT_UNIT = "kg"          # PrestaShop default; BigCommerce accepts kg or lb text is not used --
                                     # Product Weight is a plain number, weight unit is a store-level setting
                                     # in BigCommerce (Store Setup > Store Details), not per-row.

# Default PrestaShop order state IDs (out-of-the-box install). If your
# store has custom order states, edit this map or export
# ps_order_state_lang.csv and this script will prefer that instead.
DEFAULT_ORDER_STATE_MAP = {
    "1": "Awaiting check payment",
    "2": "Payment accepted",
    "3": "Processing in progress",
    "4": "Shipped",
    "5": "Delivered",
    "6": "Canceled",
    "7": "Refunded",
    "8": "Payment error",
    "9": "On backorder (paid)",
    "10": "Awaiting bank wire payment",
    "11": "Remote payment accepted",
    "12": "On backorder (not paid)",
    "13": "Payment abandoned",
    "14": "Awaiting Cash on delivery validation",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ps_to_bc")

# ----------------------------------------------------------------------
# Confirmed BigCommerce CSV headers (from live-tested imports)
# ----------------------------------------------------------------------

# Base product headers -- confirmed working on a real BigCommerce store.
# "Track Inventory" and "Product URL" are deliberately absent -- see
# module docstring above.
PRODUCT_BASE_HEADERS = [
    "Item Type", "Product Name", "Product Code/SKU", "Brand Name",
    "Product Description", "Price", "Retail Price", "Product Weight",
    "Current Stock Level", "Allow Purchases?",
    "Product Visible?", "Product Availability", "Category", "Search Keywords",
    "Page Title", "Meta Description",
    "Option Set", "Option Set Align",
]

# Image columns follow BigCommerce's documented "Product Image File - N"
# bulk-import pattern. Unlike the headers above, this has NOT been
# live-tested against your store specifically -- if the import wizard
# rejects these, drop images from the CSV and upload them separately via
# Products > Import > "Bulk Edit Images", or ask and this can be adjusted.
PRODUCT_IMAGE_SLOTS = 5
PRODUCT_IMAGE_HEADERS = []
for _i in range(1, PRODUCT_IMAGE_SLOTS + 1):
    PRODUCT_IMAGE_HEADERS += [
        f"Product Image File - {_i}",
        f"Product Image Description - {_i}",
    ]

PRODUCT_HEADERS = PRODUCT_BASE_HEADERS + PRODUCT_IMAGE_HEADERS

# Customer headers -- confirmed working. Address fields deliberately
# absent -- see module docstring above.
CUSTOMER_HEADERS = [
    "First Name", "Last Name", "Email", "Phone", "Company",
    "Accepts Marketing", "Tax Exempt", "Notes", "Customer Group",
]

# Companion address file, shaped for the Matrixify app's Customer import.
CUSTOMER_ADDRESS_HEADERS = [
    "Customer Email", "First Name", "Last Name", "Company",
    "Address Line 1", "Address Line 2", "City", "State", "Zip", "Country", "Phone",
]

# Order headers matching BigCommerce's native Order export CSV format
# exactly (column names and order), based on a real exported file. This
# is BigCommerce's own order-export layout, so a converted file lines up
# column-for-column with what a BigCommerce store itself would produce --
# useful as an import reference or for apps that expect this shape.
ORDER_HEADERS = [
    "Order ID", "Customer ID", "Customer Name", "Customer Email",
    "Customer Phone", "Order Date", "Order Status",
    "Subtotal (inc tax)", "Subtotal (ex tax)", "Tax Total",
    "Shipping Cost (inc tax)", "Shipping Cost (ex tax)", "Ship Method",
    "Handling Cost (inc tax)", "Handling Cost (ex tax)",
    "Order Total (inc tax)", "Order Total (ex tax)", "Payment Method",
    "Total Quantity", "Total Shipped", "Date Shipped",
    "Order Currency Code", "Exchange Rate", "Order Notes", "Customer Message",
    "Billing First Name", "Billing Last Name", "Billing Company",
    "Billing Street 1", "Billing Street 2", "Billing Suburb",
    "Billing State", "Billing Zip", "Billing Country", "Billing Phone",
    "Billing Email", "Billing Accounts Email Address",
    "Shipping First Name", "Shipping Last Name", "Shipping Company",
    "Shipping Street 1", "Shipping Street 2", "Shipping Suburb",
    "Shipping State", "Shipping Zip", "Shipping Country", "Shipping Phone",
    "Shipping Email", "Shipping Accounts Email Address",
    "Product Details",
    "Store Credit Redeemed", "Gift Certificate Amount Redeemed",
    "Gift Certificate Code", "Gift Certificate Expiration Date",
    "Coupon Details", "Refund Amount", "Fee Details",
]


# ----------------------------------------------------------------------
# Small utilities
# ----------------------------------------------------------------------

def load_csv(name: str) -> pd.DataFrame:
    """Load a PrestaShop table CSV from the input folder.
    Returns an empty DataFrame (with a warning) if the file is missing.
    dtype=str + keep_default_na=False prevents pandas from mangling
    SKUs/IDs/phone numbers with leading zeros or turning blanks into NaN.
    """
    path = Path(INPUT_DIR) / f"{name}.csv"
    if not path.exists():
        logger.warning(f"Missing input file: {name}.csv (skipping)")
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        logger.info(f"Loaded {name}.csv: {len(df)} rows")
        return df
    except Exception as e:
        logger.error(f"Error loading {name}.csv: {e}")
        return pd.DataFrame()


def gv(row, col, default=""):
    """Safe getter for a pandas Series/dict-like row."""
    if row is None:
        return default
    try:
        val = row.get(col, default)
    except AttributeError:
        return default
    if val is None:
        return default
    val = str(val).strip()
    return val if val else default


def format_price(value, default="0.00"):
    try:
        return f"{float(value):.2f}"
    except (ValueError, TypeError):
        return default


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return " ".join(text.split())


def slugify_keywords(*parts) -> str:
    keywords = [p.strip() for p in parts if p and p.strip()]
    return ", ".join(dict.fromkeys(keywords))  # dedupe, keep order


def write_csv(rows, headers, filename):
    out_path = Path(OUTPUT_DIR) / filename
    try:
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})
        logger.info(f"Wrote {len(rows)} rows -> {out_path}")
    except PermissionError:
        logger.error(
            f"Could not write {out_path} -- is it open in Excel? "
            f"Close the file and re-run."
        )
    return out_path


# ----------------------------------------------------------------------
# Reference data cache (mirrors the existing PrestaShop->Shopify converter)
# ----------------------------------------------------------------------

class ReferenceData:
    def __init__(self, data):
        self.data = data
        self.categories = {}
        self.manufacturers = {}
        self.attribute_groups = {}
        self.attributes = {}
        self.combinations = defaultdict(list)     # id_product_attribute -> [id_attribute,...]
        self.images = defaultdict(list)            # id_product -> [{id,url,position,cover,alt}]
        self.tags = defaultdict(list)               # id_product -> [tag names]  (only if resolvable)
        self.stock = {}                              # "prod_attr" -> qty
        self.order_states = dict(DEFAULT_ORDER_STATE_MAP)
        self.states = {}       # id_state -> name (optional ps_state.csv)
        self.countries = {}    # id_country -> name (optional ps_country_lang.csv)
        self.currencies = {}   # id_currency -> iso_code (optional ps_currency.csv)
        self._load()

    def _load(self):
        d = self.data

        # Categories
        cat_lang = d.get("ps_category_lang", pd.DataFrame())
        if not cat_lang.empty:
            for _, r in cat_lang.iterrows():
                cid = gv(r, "id_category")
                if cid:
                    self.categories[cid] = gv(r, "name")
        logger.info(f"Categories loaded: {len(self.categories)}")

        # Manufacturers
        mf = d.get("ps_manufacturer", pd.DataFrame())
        if not mf.empty:
            for _, r in mf.iterrows():
                mid = gv(r, "id_manufacturer")
                if mid:
                    self.manufacturers[mid] = gv(r, "name")
        logger.info(f"Manufacturers loaded: {len(self.manufacturers)}")

        # Attribute groups + attributes (option names/values)
        ag = d.get("ps_attribute_group", pd.DataFrame())
        if not ag.empty:
            for _, r in ag.iterrows():
                gid = gv(r, "id_attribute_group")
                name = gv(r, "name") or gv(r, "public_name")
                if gid:
                    self.attribute_groups[gid] = name
        at = d.get("ps_attribute", pd.DataFrame())
        if not at.empty:
            for _, r in at.iterrows():
                aid = gv(r, "id_attribute")
                gid = gv(r, "id_attribute_group")
                if aid:
                    self.attributes[aid] = {
                        "name": gv(r, "name"),
                        "group_id": gid,
                        "group_name": self.attribute_groups.get(gid, ""),
                    }
        logger.info(
            f"Attribute groups: {len(self.attribute_groups)}, "
            f"attributes: {len(self.attributes)}"
        )

        # Combinations: which attribute ids make up a given
        # id_product_attribute (variant row)
        comb = d.get("ps_product_attribute_combination", pd.DataFrame())
        if not comb.empty:
            for _, r in comb.iterrows():
                pa_id = gv(r, "id_product_attribute")
                a_id = gv(r, "id_attribute")
                if pa_id and a_id:
                    self.combinations[pa_id].append(a_id)
        logger.info(f"Combinations loaded for {len(self.combinations)} variant rows")

        # Images
        img = d.get("ps_image", pd.DataFrame())
        img_lang = d.get("ps_image_lang", pd.DataFrame())
        alt_by_image = {}
        if not img_lang.empty:
            for _, r in img_lang.iterrows():
                iid = gv(r, "id_image")
                if iid:
                    alt_by_image[iid] = gv(r, "legend")
        if not img.empty:
            for _, r in img.iterrows():
                pid = gv(r, "id_product")
                iid = gv(r, "id_image")
                if not pid or not iid:
                    continue
                self.images[pid].append({
                    "id": iid,
                    "url": self._image_url(iid, pid),
                    "position": gv(r, "position", "0"),
                    "cover": gv(r, "cover", "0") == "1",
                    "alt": alt_by_image.get(iid, ""),
                })
            for pid in self.images:
                self.images[pid].sort(
                    key=lambda x: (0 if x["cover"] else 1, int(x["position"] or 0))
                )
        total_imgs = sum(len(v) for v in self.images.values())
        logger.info(f"Images loaded: {total_imgs} across {len(self.images)} products")

        # Tags -- only usable if ps_tag itself carries an id_product column
        # (no ps_product_tag link table was provided in this export)
        tags_df = d.get("ps_tag", pd.DataFrame())
        if not tags_df.empty:
            if "id_product" in tags_df.columns:
                for _, r in tags_df.iterrows():
                    pid = gv(r, "id_product")
                    name = gv(r, "name")
                    if pid and name:
                        self.tags[pid].append(name)
                logger.info(f"Tags resolved for {len(self.tags)} products")
            else:
                logger.warning(
                    "ps_tag.csv has no id_product column and no ps_product_tag.csv "
                    "was provided -- tags will be skipped. Export ps_product_tag.csv "
                    "to include tags."
                )

        # Stock
        stock_df = d.get("ps_stock_available", pd.DataFrame())
        if not stock_df.empty:
            for _, r in stock_df.iterrows():
                pid = gv(r, "id_product")
                aid = gv(r, "id_product_attribute", "0")
                key = f"{pid}_{aid}"
                self.stock[key] = gv(r, "quantity", "0")
        logger.info(f"Stock rows loaded: {len(self.stock)}")

        # Prefer real order-state names if the lang table was supplied
        state_lang = d.get("ps_order_state_lang", pd.DataFrame())
        if not state_lang.empty:
            resolved = {}
            for _, r in state_lang.iterrows():
                sid = gv(r, "id_order_state")
                if sid:
                    resolved[sid] = gv(r, "name")
            if resolved:
                self.order_states = resolved
                logger.info(f"Order state names loaded from ps_order_state_lang: {len(resolved)}")

        # Optional: state/country/currency name resolution for orders.
        # None of these were in the original table list -- addresses will
        # fall back to raw PrestaShop IDs until these are provided.
        states_df = d.get("ps_state", pd.DataFrame())
        if not states_df.empty:
            for _, r in states_df.iterrows():
                sid = gv(r, "id_state")
                if sid:
                    self.states[sid] = gv(r, "iso_code") or gv(r, "name")
            logger.info(f"State names loaded from ps_state: {len(self.states)}")

        country_lang_df = d.get("ps_country_lang", pd.DataFrame())
        if not country_lang_df.empty:
            for _, r in country_lang_df.iterrows():
                cid = gv(r, "id_country")
                if cid:
                    self.countries[cid] = gv(r, "name")
            logger.info(f"Country names loaded from ps_country_lang: {len(self.countries)}")

        currency_df = d.get("ps_currency", pd.DataFrame())
        if not currency_df.empty:
            for _, r in currency_df.iterrows():
                curid = gv(r, "id_currency")
                if curid:
                    self.currencies[curid] = gv(r, "iso_code")
            logger.info(f"Currency codes loaded from ps_currency: {len(self.currencies)}")

    def _image_url(self, image_id, product_id):
        if not image_id:
            return ""
        return f"{PRESTASHOP_IMAGE_BASE_URL}/{product_id}/{image_id}.jpg"

    def stock_qty(self, product_id, attr_id="0"):
        raw = self.stock.get(f"{product_id}_{attr_id}", "0")
        try:
            qty = float(raw)
            return str(max(0, int(qty)))
        except (ValueError, TypeError):
            return "0"


# ----------------------------------------------------------------------
# Products
# ----------------------------------------------------------------------

def get_primary_lang_row(lang_df: pd.DataFrame):
    if lang_df.empty:
        return None
    filtered = lang_df[lang_df["id_lang"] == DEFAULT_LANGUAGE_ID] if "id_lang" in lang_df.columns else lang_df
    if filtered.empty:
        filtered = lang_df
    return filtered.iloc[0]


def convert_products(data, ref: ReferenceData):
    products = data.get("ps_product", pd.DataFrame())
    products_lang = data.get("ps_product_lang", pd.DataFrame())
    prod_attr = data.get("ps_product_attribute", pd.DataFrame())

    if products.empty:
        logger.warning("No ps_product.csv found/loaded -- skipping product conversion")
        return []

    rows = []
    for idx, prod in products.iterrows():
        try:
            pid = gv(prod, "id_product")
            if not pid:
                continue

            plang_rows = products_lang[products_lang["id_product"] == pid] if not products_lang.empty else pd.DataFrame()
            lang = get_primary_lang_row(plang_rows)
            if lang is None:
                logger.warning(f"Product {pid} has no ps_product_lang entry, skipping")
                continue

            name = gv(lang, "name") or f"Product {pid}"
            description = clean_html(gv(lang, "description") or gv(lang, "description_short"))
            meta_title = gv(lang, "meta_title") or name
            meta_desc = gv(lang, "meta_description")

            brand = ref.manufacturers.get(gv(prod, "id_manufacturer"), "")
            category = ref.categories.get(gv(prod, "id_category_default"), "")
            tags = ref.tags.get(pid, [])
            search_keywords = slugify_keywords(category, *tags)

            active = gv(prod, "active", "1")
            visible = "Y" if active == "1" else "N"
            availability = "available" if active == "1" else "disabled"
            available_for_order = gv(prod, "available_for_order", "1")
            allow_purchases = "Y" if available_for_order == "1" else "N"

            weight = gv(prod, "weight", "0")

            images = ref.images.get(pid, [])
            image_fields = {}
            for slot in range(PRODUCT_IMAGE_SLOTS):
                if slot < len(images):
                    image_fields[f"Product Image File - {slot + 1}"] = images[slot]["url"]
                    image_fields[f"Product Image Description - {slot + 1}"] = images[slot]["alt"]

            base_row = {
                "Item Type": "Product",
                "Product Name": name,
                "Product Code/SKU": gv(prod, "reference"),
                "Brand Name": brand,
                "Product Description": description,
                "Price": format_price(gv(prod, "price", "0")),
                "Retail Price": "",
                "Product Weight": weight,
                "Current Stock Level": ref.stock_qty(pid, "0"),
                "Allow Purchases?": allow_purchases,
                "Product Visible?": visible,
                "Product Availability": availability,
                "Category": category,
                "Search Keywords": search_keywords,
                "Page Title": meta_title,
                "Meta Description": meta_desc,
                "Option Set": "",
                "Option Set Align": "",
                **image_fields,
            }
            rows.append(base_row)

            # Variant / combination rows
            if not prod_attr.empty:
                variants = prod_attr[prod_attr["id_product"] == pid]
                for _, vrow in variants.iterrows():
                    pa_id = gv(vrow, "id_product_attribute")
                    sku = gv(vrow, "reference") or f"{gv(prod, 'reference')}-{pa_id}"
                    price_impact = gv(vrow, "price", "0")
                    try:
                        variant_price = float(gv(prod, "price", "0")) + float(price_impact or 0)
                    except (ValueError, TypeError):
                        variant_price = gv(prod, "price", "0")
                    sku_row = {
                        "Item Type": "SKU",
                        "Product Code/SKU": sku,
                        "Price": format_price(variant_price),
                        "Retail Price": "",
                        "Product Weight": gv(vrow, "weight", weight),
                        "Current Stock Level": ref.stock_qty(pid, pa_id),
                    }
                    rows.append(sku_row)

            if (idx + 1) % 100 == 0:
                logger.info(f"Processed {idx + 1} products...")

        except Exception as e:
            logger.error(f"Error processing product {gv(prod, 'id_product', 'unknown')}: {e}")
            continue

    return rows


# ----------------------------------------------------------------------
# Customers
# ----------------------------------------------------------------------

def convert_customers(data):
    customers = data.get("ps_customer", pd.DataFrame())
    if customers.empty:
        logger.warning("No ps_customer.csv found/loaded -- skipping customer conversion")
        return []

    rows = []
    for _, c in customers.iterrows():
        email = gv(c, "email")
        if not email:
            continue
        newsletter = gv(c, "newsletter", "0")
        rows.append({
            "First Name": gv(c, "firstname"),
            "Last Name": gv(c, "lastname"),
            "Email": email,
            "Phone": "",  # PrestaShop keeps phone on ps_address, not ps_customer
            "Company": gv(c, "company"),
            "Accepts Marketing": "Yes" if newsletter == "1" else "No",
            "Tax Exempt": "",
            "Notes": gv(c, "note"),
            "Customer Group": "",
        })
    return rows


def convert_addresses(data):
    addresses = data.get("ps_address", pd.DataFrame())
    customers = data.get("ps_customer", pd.DataFrame())
    if addresses.empty:
        logger.warning("No ps_address.csv found/loaded -- skipping address conversion")
        return []

    email_by_customer = {}
    if not customers.empty:
        for _, c in customers.iterrows():
            cid = gv(c, "id_customer")
            if cid:
                email_by_customer[cid] = gv(c, "email")

    rows = []
    for _, a in addresses.iterrows():
        if gv(a, "deleted", "0") == "1":
            continue
        cid = gv(a, "id_customer")
        email = email_by_customer.get(cid, "")
        if not email:
            continue
        rows.append({
            "Customer Email": email,
            "First Name": gv(a, "firstname"),
            "Last Name": gv(a, "lastname"),
            "Company": gv(a, "company"),
            "Address Line 1": gv(a, "address1"),
            "Address Line 2": gv(a, "address2"),
            "City": gv(a, "city"),
            "State": gv(a, "id_state"),   # raw PrestaShop state id -- map to name if you
                                            # export ps_state.csv; ask and this can be resolved
            "Zip": gv(a, "postcode"),
            "Country": gv(a, "id_country"),  # raw PrestaShop country id -- same note as State
            "Phone": gv(a, "phone") or gv(a, "phone_mobile"),
        })
    return rows


# ----------------------------------------------------------------------
# Orders (header-only -- see NOTE 3 in module docstring)
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Orders -- matches BigCommerce's native Order export CSV format exactly
# ----------------------------------------------------------------------

def format_ps_date(raw: str) -> str:
    """PrestaShop date_add/date_upd come as 'YYYY-MM-DD HH:MM:SS'.
    BigCommerce's own order export uses 'DD/MM/YYYY' with no time
    component (matches the sample file), so reformat to that.
    Falls back to the raw value if it doesn't parse.
    """
    if not raw:
        return ""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return raw


def build_address_lookup(addresses: pd.DataFrame):
    """id_address -> dict of raw ps_address fields, for billing/shipping lookup."""
    lookup = {}
    if addresses.empty:
        return lookup
    for _, a in addresses.iterrows():
        aid = gv(a, "id_address")
        if aid:
            lookup[aid] = a
    return lookup


def address_fields(addr_row, ref: "ReferenceData", prefix: str) -> dict:
    """Build the Billing */Shipping * columns for one address row.
    addr_row may be None if the address id didn't resolve.
    """
    if addr_row is None:
        return {
            f"{prefix} First Name": "", f"{prefix} Last Name": "",
            f"{prefix} Company": "", f"{prefix} Street 1": "",
            f"{prefix} Street 2": "", f"{prefix} Suburb": "",
            f"{prefix} State": "", f"{prefix} Zip": "",
            f"{prefix} Country": "", f"{prefix} Phone": "",
            f"{prefix} Email": "", f"{prefix} Accounts Email Address": "",
        }
    state_id = gv(addr_row, "id_state")
    country_id = gv(addr_row, "id_country")
    return {
        f"{prefix} First Name": gv(addr_row, "firstname"),
        f"{prefix} Last Name": gv(addr_row, "lastname"),
        f"{prefix} Company": gv(addr_row, "company"),
        f"{prefix} Street 1": gv(addr_row, "address1"),
        f"{prefix} Street 2": gv(addr_row, "address2"),
        f"{prefix} Suburb": gv(addr_row, "city"),
        f"{prefix} State": ref.states.get(state_id, state_id),
        f"{prefix} Zip": gv(addr_row, "postcode"),
        f"{prefix} Country": ref.countries.get(country_id, country_id),
        f"{prefix} Phone": gv(addr_row, "phone") or gv(addr_row, "phone_mobile"),
        f"{prefix} Email": "",  # ps_address has no email column
        f"{prefix} Accounts Email Address": "",
    }


def build_product_details(order_id: str, order_detail: pd.DataFrame) -> (str, int):
    """Build the pipe-delimited Product Details string exactly matching
    BigCommerce's export format:
        Product ID: X, Product Qty: Y, Product SKU: Z, Product Name: N,
        Product Weight: W, Product Variation Details: , Product Unit Price: P,
        Product Total Price: T
    joined with '|' between line items. Returns (string, total_quantity).
    Requires an optional ps_order_detail.csv (not in the original table
    list) -- returns ("", 0) if that file wasn't provided.
    """
    if order_detail.empty:
        return "", 0
    lines = order_detail[order_detail["id_order"] == order_id]
    if lines.empty:
        return "", 0

    parts = []
    total_qty = 0
    for _, l in lines.iterrows():
        qty = gv(l, "product_quantity", "0")
        try:
            total_qty += int(float(qty))
        except (ValueError, TypeError):
            pass
        unit_price = gv(l, "unit_price_tax_excl") or gv(l, "product_price")
        total_price = gv(l, "total_price_tax_excl")
        if not total_price:
            try:
                total_price = format_price(float(unit_price or 0) * float(qty or 0))
            except (ValueError, TypeError):
                total_price = ""
        parts.append(
            f"Product ID: {gv(l, 'product_id')}, "
            f"Product Qty: {qty}, "
            f"Product SKU: {gv(l, 'product_reference')}, "
            f"Product Name: {gv(l, 'product_name')}, "
            f"Product Weight: {gv(l, 'product_weight')}, "
            f"Product Variation Details: {gv(l, 'product_attributes_summary')}, "
            f"Product Unit Price: {format_price(unit_price)}, "
            f"Product Total Price: {format_price(total_price)}"
        )
    return "|".join(parts), total_qty


def convert_orders(data, ref: "ReferenceData"):
    orders = data.get("ps_orders", pd.DataFrame())
    customers = data.get("ps_customer", pd.DataFrame())
    addresses = data.get("ps_address", pd.DataFrame())
    order_detail = data.get("ps_order_detail", pd.DataFrame())

    if orders.empty:
        logger.warning("No ps_orders.csv found/loaded -- skipping order conversion")
        return []

    if order_detail.empty:
        logger.warning(
            "No ps_order_detail.csv provided -- 'Product Details' and "
            "'Total Quantity' will be blank/0 for every order. Export "
            "ps_order_detail.csv from PrestaShop to populate line items."
        )

    email_by_customer = {}
    name_by_customer = {}
    if not customers.empty:
        for _, c in customers.iterrows():
            cid = gv(c, "id_customer")
            if cid:
                email_by_customer[cid] = gv(c, "email")
                name_by_customer[cid] = f"{gv(c, 'firstname')} {gv(c, 'lastname')}".strip()

    address_lookup = build_address_lookup(addresses)

    rows = []
    for _, o in orders.iterrows():
        cid = gv(o, "id_customer")
        order_id = gv(o, "id_order")
        state_id = gv(o, "current_state")

        billing_addr = address_lookup.get(gv(o, "id_address_invoice"))
        shipping_addr = address_lookup.get(gv(o, "id_address_delivery"))

        product_details, total_qty = build_product_details(order_id, order_detail)

        subtotal_inc = gv(o, "total_products_wt") or gv(o, "total_products")
        subtotal_ex = gv(o, "total_products")
        ship_inc = gv(o, "total_shipping_tax_incl") or gv(o, "total_shipping")
        ship_ex = gv(o, "total_shipping_tax_excl") or gv(o, "total_shipping")

        row = {
            "Order ID": order_id,
            "Customer ID": cid,
            "Customer Name": name_by_customer.get(cid, ""),
            "Customer Email": email_by_customer.get(cid, ""),
            "Customer Phone": (
                gv(billing_addr, "phone") if billing_addr is not None else ""
            ) or (gv(billing_addr, "phone_mobile") if billing_addr is not None else ""),
            "Order Date": format_ps_date(gv(o, "date_add")),
            "Order Status": ref.order_states.get(state_id, state_id),
            "Subtotal (inc tax)": format_price(subtotal_inc),
            "Subtotal (ex tax)": format_price(subtotal_ex),
            "Tax Total": format_price(
                float(gv(o, "total_paid_tax_incl", "0") or 0) - float(gv(o, "total_paid_tax_excl", "0") or 0)
            ),
            "Shipping Cost (inc tax)": format_price(ship_inc),
            "Shipping Cost (ex tax)": format_price(ship_ex),
            "Ship Method": gv(o, "carrier_name") or gv(o, "id_carrier"),
            "Handling Cost (inc tax)": "0.00",   # not tracked at order level in ps_orders
            "Handling Cost (ex tax)": "0.00",
            "Order Total (inc tax)": format_price(gv(o, "total_paid")),
            "Order Total (ex tax)": format_price(gv(o, "total_paid_tax_excl")),
            "Payment Method": gv(o, "payment"),
            "Total Quantity": total_qty,
            "Total Shipped": "0",   # ps_orders has no shipped-qty tracking
            "Date Shipped": "",     # would come from ps_order_history / carrier tracking, not in this export
            "Order Currency Code": ref.currencies.get(gv(o, "id_currency"), gv(o, "id_currency")),
            "Exchange Rate": gv(o, "conversion_rate", "1.0000000000"),
            "Order Notes": "",       # PrestaShop order-level notes live in ps_message, not exported here
            "Customer Message": "",  # same as above
            "Product Details": product_details,
            "Store Credit Redeemed": "0.00",
            "Gift Certificate Amount Redeemed": "0.00",
            "Gift Certificate Code": "",
            "Gift Certificate Expiration Date": "",
            "Coupon Details": "",     # would come from ps_order_cart_rule, not in this export
            "Refund Amount": "0.00",
            "Fee Details": "",
        }
        row.update(address_fields(billing_addr, ref, "Billing"))
        row.update(address_fields(shipping_addr, ref, "Shipping"))
        rows.append(row)

    return rows


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

ALL_TABLES = [
    "ps_product", "ps_product_lang", "ps_manufacturer", "ps_category_lang",
    "ps_attribute", "ps_attribute_group", "ps_product_attribute",
    "ps_product_attribute_combination", "ps_stock_available",
    "ps_image", "ps_image_lang", "ps_tag", "ps_feature_product",
    "ps_customer", "ps_address", "ps_orders",
    # optional, used opportunistically if present
    "ps_product_tag", "ps_order_state_lang", "ps_order_detail",
    "ps_state", "ps_country_lang", "ps_currency",
]


def main():
    Path(INPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    logger.info("Starting PrestaShop -> BigCommerce migration")
    logger.info(f"Input directory: {INPUT_DIR}")
    logger.info(f"Output directory: {OUTPUT_DIR}")

    data = {name: load_csv(name) for name in ALL_TABLES}

    ref = ReferenceData(data)

    product_rows = convert_products(data, ref)
    write_csv(product_rows, PRODUCT_HEADERS, "bigcommerce_products.csv")

    customer_rows = convert_customers(data)
    write_csv(customer_rows, CUSTOMER_HEADERS, "bigcommerce_customers.csv")

    address_rows = convert_addresses(data)
    write_csv(address_rows, CUSTOMER_ADDRESS_HEADERS, "bigcommerce_addresses_bulk_import.csv")

    order_rows = convert_orders(data, ref)
    write_csv(order_rows, ORDER_HEADERS, "bigcommerce_orders_bulk_import.csv")

    logger.info("Migration complete.")
    logger.info(
        f"Products: {len([r for r in product_rows if r.get('Item Type') == 'Product'])} "
        f"(+ {len([r for r in product_rows if r.get('Item Type') == 'SKU'])} variant rows), "
        f"Customers: {len(customer_rows)}, Addresses: {len(address_rows)}, Orders: {len(order_rows)}"
    )


if __name__ == "__main__":
    main()
