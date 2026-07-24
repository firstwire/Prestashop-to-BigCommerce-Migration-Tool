We have created a free tool to convert PrestaShop data into BigCommerce-compatible format.
You can use this tool to convert your product, customer, address, and order data into files that are ready to import into BigCommerce.
Once converted, you can simply upload the new data files to BigCommerce.

Please see the detailed instructions at :

See the code and guide below.


**Step 1 — Install Python (one-time setup)**

Python is the free program that runs the script. If you already have Python installed, skip to Step 2.
1. Go to python.org/downloads in your web browser.
2. Click the yellow "Download Python" button.
3. Open the downloaded file and run the installer.

**Important**

On the first install screen, tick the box that says 
"Add Python to PATH" before clicking Install.

4. Click Install Now and wait for it to finish.

To check it worked, open your terminal (Command Prompt on Windows, Terminal on Mac) and type:
 python --version

If you see a version number like "Python 3.12.0", you are ready for Step 2.


**Step 2 — Install the Required Add-ons**

The script needs one free add-on package to read and write CSV files. Open your terminal and type this single line: pip install pandas --break-system-packages

Press Enter and wait a few seconds for it to finish. You only need to do this once.

**Step 3 — Save Your Files in One Folder**

Create a new folder on your Desktop (for example, "Prestashop_to_Bigcommerce_migration"). 
Inside it, create another folder called "input" — this is where all your PrestaShop table exports will go. 
Your folder structure should look like this: Prestashop_to_Bigcommerce_migration/
 prestashop_to_bc_converter.py 
 input/ 
  ps_product.csv 
  ps_product_lang.csv 
  ps_manufacturer.csv 
  ps_category_lang.csv 
  ps_attribute.csv 
  ps_attribute_group.csv 
  ps_product_attribute.csv 
  ps_product_attribute_combination.csv 
  ps_stock_available.csv ps_image.csv 
  ps_image_lang.csv ps_tag.csv 
  ps_product_tag.csv 
  ps_feature_product.csv 
  ps_customer.csv 
  ps_address.csv 
  ps_orders.csv 
  ps_order_detail.csv 
  ps_state.csv 
  ps_country_lang.csv 
  ps_currency.csv 
  ps_order_state_lang.csv
 output/
   
Place the script file directly inside "Prestashop_to_Bigcommerce_migration", and place your PrestaShop CSV exports (one CSV per database table, named exactly like the table) inside the "input" folder:

You do not need every file. At minimum:

• input/ps_product.csv + ps_product_lang.csv (if migrating products)

• input/ps_customer.csv (if migrating customers)

• input/ps_address.csv (if migrating addresses)

• input/ps_orders.csv (if migrating orders)

The rest are optional and each unlock one extra piece of detail — brand names, categories, variants, stock, images, tags, real state/country/currency names, order line items, and your store's real order status names. If a file is missing, the script simply skips that piece and logs a warning; it will not stop the conversion.

**Step 4 — Configure the Script (Optional)**

Unlike a config file, this script keeps its settings inside the script itself. If you need to customize anything, open prestashop_to_bc_converter.py in a text editor and look near the top for:

PRESTASHOP_STORE_URL = "https://your-prestashop-store.com" (change this to your real store address — it's used to build the web address for each product image)

DEFAULT_LANGUAGE_ID = "1" (change this if your PrestaShop catalog's main language isn't language ID 1)

DEFAULT_ORDER_STATE_MAP (a list of PrestaShop's default order status names — only edit this, or export ps_order_state_lang.csv instead, if your store uses custom order statuses)

**Step 5 — Run the Script**

5. Open your terminal.
6. Navigate to the folder you created. For example: cd Desktop/Prestashop_to_Bigcommerce_migration
7. Run the script by typing: 

python prestashop_to_bc_converter.py


**Step 6 — Find Your Converted Files**

Once the script finishes, it creates a new folder called "output" inside your project folder. Open it to find:
File Name What It Contains
bigcommerce_products.csv Your products (and variant SKU rows), ready for BigCommerce

bigcommerce_customers.csv Your customers, ready for BigCommerce

bigcommerce_addresses_bulk_import.csv Your customer addresses, ready for the Matrixify app

bigcommerce_orders_bulk_import.csv Your orders, matching BigCommerce's own order-export layout

**Step 7 — Import Into BigCommerce**

Products

8. In BigCommerce Admin, go to Products.
9. Click Import.
10. Choose the file bigcommerce_products.csv and upload it.
11. Review the preview, then confirm the import.

Note: this file has no "Track Inventory" or "Product URL" columns on purpose — BigCommerce applies its own defaults for these. Variant rows only carry SKU, Price, Retail Price, and Weight.

Customers

12. In BigCommerce Admin, go to Customers.
13. Click Import.
14. Choose the file bigcommerce_customers.csv and upload it.
15. Review the preview, then confirm the import.

Note: this file has no address columns on purpose — BigCommerce's built-in customer importer rejects rows that include address data. Addresses are imported separately below.

Orders (needs a bulk-import app)

BigCommerce does not allow orders to be imported directly. bigcommerce_orders_bulk_import.csv is laid out to match BigCommerce's own native order-export format column-for-column, so it can be mapped manually into whichever order-import app you use from BigCommerce's Data Transfer / Migration Services app category. Unlike the products and addresses files, this file's format has not been verified against one specific app's template, so check your chosen app's column-mapping screen.

**Troubleshooting — Common Questions**

Problem - Solution

"python is not recognized" Reinstall Python and make sure to tick "Add Python to PATH"

"No module named pandas" Run: pip install pandas --break-system-packages

File not found / missing table warnings Make sure each CSV is in the input folder and named exactly like its PrestaShop table (e.g. ps_product.csv). Warnings for optional tables are expected if you didn't export them.

Tags aren't showing up Export ps_product_tag.csv, or make sure ps_tag.csv itself has an id_product column

Product features aren't included This isn't supported yet regardless of which tables you export

Product Details is blank / Total Quantity is 0 on orders Export ps_order_detail.csv to include line items

Billing/Shipping State or Country shows a number instead of a name Export ps_state.csv and/or ps_country_lang.csv

Order Currency Code shows a number instead of a code like USD Export ps_currency.csv

Order Status names look wrong Export ps_order_state_lang.csv, or edit DEFAULT_ORDER_STATE_MAP in the script

Some images are missing in BigCommerce Check the PRESTASHOP_STORE_URL setting in the script — this is used to build each image's web address

Order import fails Make sure you are using a bulk order-import app — BigCommerce cannot import orders directly

Quick Reference — Every Time You Run It

Open terminal in your project folder
Type: cd Desktop/Prestashop_to_Bigcommerce_migration
Type: python prestashop_to_bc_converter.py
Find your results in the output folder

That's it — no coding required. If you run into any issue not listed above, check that your CSV files were exported correctly from PrestaShop and try again.

At FirstWire, we can do the complete migration and make sure that your new Shopify store is setup properly and optimized for Design, User Experience, Performance, SEO and CRO.

Please Contact Us for a custom proposal at https://firstwireapp.com/get-a-quotation/

You can also check our other BigCommerce Services at https://firstwireapp.com/e-commerce/bigcommerce/
