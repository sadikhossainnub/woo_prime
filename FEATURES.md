# Woo Prime — Complete Feature Documentation & Technical Guide

**Woo Prime** is an enterprise-grade, bi-directional integration app between **ERPNext** (v15/v16) and **WooCommerce** (WordPress). It powers end-to-end synchronization for Products, Template Variations, Stock, Prices, Categories, Attributes, Tags, Shipping Rules, Orders, and Discounts with real-time Webhooks, background workers, and fail-safe database recovery.

---

## Table of Contents
1. [Core Architecture & Background Processing](#1-core-architecture--background-processing)
2. [Product & Item Management](#2-product--item-management)
3. [Attributes & Variation Taxonomy](#3-attributes--variation-taxonomy)
4. [Product Categories & Tags](#4-product-categories--tags)
5. [Shipping Methods & Rules Synchronization](#5-shipping-methods--rules-synchronization)
6. [Automated Order Processing & Webhooks](#6-automated-order-processing--webhooks)
7. [Stock & Price Synchronization](#7-stock--price-synchronization)
8. [WordPress Connector Plugin](#8-wordpress-connector-plugin)
9. [Diagnostics, Logging & Self-Healing Database](#9-diagnostics-logging--self-healing-database)
10. [Configuration & Setup Reference](#10-configuration--setup-reference)

---

## 1. Core Architecture & Background Processing

### High-Performance Non-Blocking Design
- **Background Worker Engine**: All heavy tasks (fetching hundreds of products, bulk publishing variable items, pushing attributes) are enqueued to Frappe's background worker (`queue="long"` with 30–60 minute timeouts).
- **WebSocket Real-Time Progress Notifications**: Every background sync broadcasts live events (`woo_fetch_progress`, `woo_publish_progress`) to the desk UI. Users see live percentage bars, processed counts, and status toasts without freezing the browser or hitting gateway timeouts.
- **Resilient WooCommerce REST Client (`woo_api.py`)**:
  - Full support for WooCommerce REST API v3.
  - Configurable timeout (default 60s) preventing `Read timed out` exceptions on shared or high-latency WordPress hosts.
  - Automatic fallback between Query Param Authentication (`consumer_key`/`consumer_secret`) and Basic Auth headers.
  - Automatic URL normalization (handles trailing slashes, `wp-json` route prefixing).
  - Custom User-Agent support (default `curl/8.5.0`) to bypass Cloudflare WAF, Wordfence, or ModSecurity bot challenges.

---

## 2. Product & Item Management

### 2.1 Publishing Items (ERPNext ➔ WooCommerce)
- **Background Single Publish**:
  - Available on **`Woo Item`** form and **`Item` (ERPNext Item Master)** form via the **`Publish to WooCommerce`** action button.
  - Runs in the background queue; notifies the user via real-time alert and reloads the document with WooCommerce Product ID and live permalink.
- **Bulk Publishing**:
  - In **`Woo Item List View`**, select any number of items and click **`Actions ➔ Bulk Publish to WooCommerce`**.
  - A real-time progress dialog tracks: Total Items, Current Item, Successful Count, and Failed Count with a progress bar.
- **Supported Product Types**:
  1. **Simple Products**:
     - Synchronizes SKU, title, descriptions, regular price, sale price, weight, stock quantity, stock status (`instock`/`outofstock`).
     - Uploads primary item image and slideshow images.
     - Attaches WooCommerce categories and tags.
  2. **Template & Variable Products**:
     - Recognizes ERPNext Template items (`has_variants = 1`) and publishes as WooCommerce `variable` products.
     - Automatically generates global product attributes (`pa_*`) and term values for swatch buttons.
     - **Batch Variations API**: Uploads all variant items (`variant_of`) in a single WooCommerce Batch API call (`/wp-json/wc/v3/products/<id>/variations/batch`) for ultra-fast creation.
     - Generates child `Woo Item` records for each variant and links variation IDs.

### 2.2 Fetching Products (WooCommerce ➔ ERPNext)
- **Batched Product Fetch**:
  - Fetches products in configurable page sizes (10 to 100 items per request).
  - Auto-matches and links with ERPNext Items by SKU, Item Code, or Name.
  - **Auto-create Missing Items**: Automatically creates ERPNext Item Master records under the default Item Group if no local match is found.
  - **SKU Fallback**: Generates `WC-{id}` if WooCommerce products do not have an SKU assigned.
  - **Tag Attachment**: Extracts product tags from WooCommerce and attaches them directly to ERPNext Items (`_user_tags`).

---

## 3. Attributes & Variation Taxonomy

### 3.1 ERPNext Attribute ➔ WooCommerce Global Attribute Push
- **Single Attribute Push**:
  - In ERPNext **`Item Attribute`** master, click **`WooCommerce ➔ Push to WooCommerce`**.
  - Automatically creates a Global Taxonomy Attribute (e.g., `pa_color`, `pa_size`) on WooCommerce, uploads all term values, and maps them in `Woo Settings`.
- **Bulk Attributes Push**:
  - In **`Woo Settings`**, click **`Sync ➔ Push Attributes to WooCommerce`** or use the **`Push All Attributes to WooCommerce`** button.
  - Runs as a background worker with live progress tracking dialog.
- **Strict Unique Abbreviation Sanitizer**:
  - Automatically cleans HTML entities (`&amp;` ➔ `&`).
  - Generates strictly unique, uppercase abbreviations (<=10 characters) for `Item Attribute Value` rows, permanently resolving `Abbreviation must appear only once` errors.
- **Auto-Mapping**:
  - **`Auto-Map Attributes by Name`** scans all ERPNext Item Attributes and WooCommerce Attributes to link matching pairs automatically.

---

## 4. Product Categories & Tags

### 4.1 Product Categories
- Synchronizes the entire WooCommerce Category taxonomy (Hierarchical parent-child tree).
- Links ERPNext `Item Group` records to `Woo Category`.
- Assigns primary and secondary categories to WooCommerce products during publishing.
- Runs in background with live category count updates.

### 4.2 Product Tags
- **3-Tier Fetch Strategy**:
  1. WooCommerce REST API `products/tags?hide_empty=false`.
  2. Embedded tags scan from existing WooCommerce products.
  3. WordPress Core REST API (`/wp/v2/tags`) fallback.
- Stores tags in `Woo Tag` doctype and creates standard Frappe `Tag` records.
- Preserves tags both ways: incoming tags attach to ERPNext Items, and outgoing ERPNext tags push to WooCommerce products.

---

## 5. Shipping Methods & Rules Synchronization

### 5.1 Fetching Shipping Data
- Fetches all configured **Shipping Zones**, **Zone Methods** (Flat Rate, Free Shipping, Local Pickup), and custom rates from WooCommerce in the background.
- Populates the **`Shipping Zone / Method Mappings`** table in `Woo Settings`.

### 5.2 Flexible Shipping Charge Sync Modes
- **Line Item Mode**:
  - Adds shipping charges as a distinct line item in the Sales Order `items` table using the configured **Shipping Charges Item**.
  - Ideal for delivery tracking and stock valuation reports.
- **Taxes and Charges Table Mode**:
  - Adds shipping fees directly to the **Sales Taxes and Charges** table mapped to a designated **Shipping Charge Account**.
- **Shipping Rule Mapping**:
  - Maps WooCommerce methods (e.g., "Inside Dhaka", "Outside Dhaka") directly to ERPNext `Shipping Rule` records.
  - Includes **Default Shipping Rule** fallback for unmatched methods.

---

## 6. Automated Order Processing & Webhooks

### 6.1 Real-Time Webhook Listener
- Endpoint: `/api/method/woo_prime.api.webhook.handle_order`
- **HMAC-SHA256 Signature Verification**: Evaluates the `X-WC-Webhook-Signature` header against the configured **Webhook Secret** using a secure fail-closed security policy.
- Webhook payloads are processed instantly in the background queue (`frappe.enqueue`) to respond with `200 OK` to WooCommerce in milliseconds.

### 6.2 Intelligent Sales Order Creation
- **Customer & Contact Management**:
  - Normalizes Bangladeshi and international mobile numbers.
  - Finds or creates customer by email or phone.
  - Generates separate **Billing** and **Shipping** address records and links them to the Sales Order.
- **Discounts, Coupons & Loyalty Points**:
  - Records applied WooCommerce coupon codes in the Sales Order `po_no`.
  - Maps cart discounts directly to `discount_amount` on Grand Total.
  - Detects Loyalty Points coupons and maps them to `loyalty_amount`.
- **Order Automation**:
  - Configurable **Sales Type** (Inbound, Outbound, Website, Ads).
  - Configurable **Auto Create Customer** and **Auto Submit Sales Order**.
  - Optional Email Notifications on new orders.
  - Sets Delivery Date automatically (+7 days default).

### 6.3 Background Order Reconciliation
- In case webhooks drop due to server downtime or network issues:
  - Configurable background scheduler runs every 5 to 60 minutes.
  - Configurable **Reconciliation Lookback (1–30 Days)** to scan for missing WooCommerce orders and auto-create Sales Orders idempotently.

---

## 7. Stock & Price Synchronization

### 7.1 Stock Synchronization
- **Event-Driven Auto-Push**: Automatically triggers stock updates to WooCommerce whenever a Stock Entry, Delivery Note, or Purchase Receipt is submitted in ERPNext.
- **Scheduled Sync**: Configurable intervals:
  - Every 15 Minutes
  - Every 30 Minutes
  - Hourly (Default)
  - Every 6 Hours
  - Daily
- **Warehouse Aggregation**: Calculates stock levels based on configured default warehouse or aggregated company inventory.
- **Manual Push**: **`Sync Stock`** button on individual Woo Items.

### 7.2 Price Synchronization
- **Scheduled Sync**: Configurable intervals (Hourly, Every 6 Hours, Daily).
- Reads rates from the configured **Default Price List** (e.g., "Standard Selling").
- Automatic fallback for variant items from the template item's price list rate.
- **Manual Push**: **`Sync Price`** button on individual Woo Items.

---

## 8. WordPress Connector Plugin

The downloadable **Woo Prime Connector** plugin (`woo-prime-connector.zip`) extends WordPress with direct ERPNext communication:
- **Download**: One-click download button in `Woo Settings` (**`Download WordPress Plugin`**).
- **Cart Price Calculation**: Calls ERPNext in real-time to compute BOGO (Buy One Get One), volume tier discounts, and customer-specific Pricing Rules in WooCommerce cart.
- **Loyalty Points Redemption**: Allows customers to view their live ERPNext Loyalty Points balance at WooCommerce checkout and redeem points as instant order coupons.
- **Token Authentication**: Secured via `X-Woo-Prime-Token` shared secret header.

---

## 9. Diagnostics, Logging & Self-Healing Database

### 9.1 Self-Healing Database Engine (`woo_prime/utils.py`)
- **Automatic Crash Repair**: Catches MySQL Error 144 (`tabError Log is marked as crashed`) and automatically executes `REPAIR TABLE \`tabError Log\`` without interrupting sync jobs or crashing worker threads.
- **Safe Error Logger (`safe_log_error`)**: Ensures error logging never throws secondary exceptions even if MariaDB encounters deadlocks or table lock collisions.
- **Table Integrity Enforcer**: Automatically verifies and regenerates DocType schema tables (`tabWoo Attribute`, `tabWoo Tag`, `tabWoo Attribute Mapping`) if database migrations were skipped.

### 9.2 Woo Sync Log DocType
- Detailed incoming and outgoing HTTP logs.
- Captures Request URL, Method, Payload JSON, Response Code, Response Body Preview, and Traceback.
- **1-Click Retry**: **`Retry Sync`** button directly on failed sync logs to replay the operation once the underlying issue is resolved.

---

## 10. Configuration & Setup Reference

### Summary of Key Settings in `Woo Settings`:

| Setting Field | Description | Recommended Value |
|---|---|---|
| **Enable WooCommerce** | Master toggle to enable integration | Checked (`1`) |
| **WooCommerce Site URL** | HTTPS URL of your WordPress store | `https://yourdomain.com` |
| **Consumer Key / Secret** | Generated from WooCommerce REST API keys | Read/Write Key |
| **API Shared Secret** | Shared token for WordPress Connector plugin | Auto-generated 40-char token |
| **Webhook Delivery URL** | URL to paste in WooCommerce Webhook settings | `https://<erp-url>/api/method/woo_prime.api.webhook.handle_order` |
| **Webhook Secret** | Secret key for verifying HMAC-SHA256 signatures | Strong password |
| **Default Company** | ERPNext Company for transactions | Your Company |
| **Default Warehouse** | Warehouse used for stock calculations | Finished Goods / Stores |
| **Default Price List** | Price list used for price sync | Standard Selling |
| **Default Item Group** | Item group for auto-created items | Products / All Item Groups |
| **Shipping Sync Mode** | Line Item vs Taxes & Charges Table | Line Item |
| **Order Sync Interval** | Minutes between background order checks | 5 Minutes |
| **Order Reconciliation Lookback**| Days to check for missed orders | 2 Days |

---

*Developed by prime tech bd & Contributors. Licensed under MIT.*

