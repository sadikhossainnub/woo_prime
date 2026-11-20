# Woo Prime — Automatic Sync বিস্তারিত

> এই ডকুমেন্টে Woo Prime অ্যাপের সমস্ত স্বয়ংক্রিয় (automatic) sync প্রক্রিয়ার বিবরণ দেওয়া হয়েছে।

---

## ১. Scheduled Tasks (নির্ধারিত সময়ের কাজ)

Frappe scheduler প্রতি **৫ মিনিটে** নিচের ৩টি কাজ চেক করে:

```
hooks.py → scheduler_events → cron: "*/5 * * * *"
```

| Scheduled Function | কী করে |
|---|---|
| `scheduled_order_sync` | WooCommerce অর্ডার pull করে |
| `scheduled_stock_sync` | সব পণ্যের স্টক push করে |
| `scheduled_price_sync` | সব পণ্যের দাম push করে |

> [!NOTE]
> প্রতিটি scheduled function শুধু **চেক** করে — আসল কাজ background queue-এ `frappe.enqueue()` করে পাঠায়। এতে scheduler কখনো block হয় না।

---

## ২. Scheduled Sync-এর শর্তাবলী (Guard Checks)

প্রতিটি scheduled sync চালু হওয়ার আগে ৩টি শর্ত পরীক্ষা করে:

```
১. settings.enabled == True  (Woo Settings চালু আছে?)
২. enable_xxx_sync == True   (ওই নির্দিষ্ট sync চালু আছে?)
৩. _is_within_sync_window()  (এখন sync করার সময় উইন্ডোতে আছে?)
```

### Sync Window কী?
`Woo Settings`-এ `enable_auto_sync_schedule` চালু থাকলে নির্দিষ্ট সময়ের মধ্যে (যেমন রাত ১০টা থেকে সকাল ৬টা) sync বন্ধ রাখা যায়।
- Schedule বন্ধ থাকলে → সবসময় True (কোনো বাধা নেই)
- সময় সেট না থাকলে → সবসময় True

---

## ৩. Order Sync (অর্ডার সিঙ্ক)

### স্বয়ংক্রিয় প্রবাহ:
```
প্রতি ৫ মিনিটে →
  scheduled_order_sync()
        ↓ (background queue)
  auto_sync_orders()
        ↓
  WooCommerce API → orders fetch (after= শেষ sync সময়)
        ↓
  প্রতিটি নতুন order → sync_order() → ERPNext Sales Order তৈরি
```

### মূল নিয়ম:
- শুধু নতুন order আনে — যে order আগে নেই (`woo_order_id` দিয়ে চেক)
- `order_sync_interval` (মিনিট) সময় না পেরোলে skip করে
- যে statuses sync করবে: `processing`, `completed` (Woo Settings থেকে পরিবর্তনযোগ্য)
- **Daily Reconciliation:** প্রতিদিন একবার `reconcile_orders()` চলে — শেষ ২ দিনের missed order ধরে

### Settings:
| Field | Default | কাজ |
|---|---|---|
| `enable_order_sync` | চালু | অর্ডার sync on/off |
| `order_sync_interval` | ৫ মিনিট | কতক্ষণ পর পর চেক করবে |
| `sync_order_statuses` | processing, completed | কোন status এর order আনবে |
| `reconcile_days` | ২ দিন | Daily reconciliation-এ কতদিন পিছনে যাবে |

---

## ৪. Stock Sync (স্টক সিঙ্ক)

### দুই উপায়ে স্টক sync হয়:

#### ক) Real-Time (তাৎক্ষণিক)
```
ERPNext Bin পরিবর্তন হলে →
  on_bin_update() [doc_events hook]
        ↓ (শর্ত: enabled + enable_stock_sync + সঠিক warehouse)
  frappe.enqueue → sync_stock_for_item_code(item_code)
        ↓
  WooCommerce-এ stock update
```

> [!TIP]
> `default_warehouse` সেট থাকলে শুধু সেই warehouse-এর stock পরিবর্তনে sync হবে। অন্য warehouse থেকে stock নাড়ালে sync হবে না।

#### খ) Scheduled (নির্ধারিত সময়ে)
```
প্রতি ৫ মিনিটে →
  scheduled_stock_sync()
        ↓ (stock_sync_interval সময় পেরোলে)
  sync_all_stock()
        ↓
  সব published Woo Item (sync_stock=1) → 100টা করে batch → WooCommerce stock update
```

### Stock Sync Interval Options:
| Interval | সময় |
|---|---|
| Every 15 Minutes | ১৫ মিনিট |
| Every 30 Minutes | ৩০ মিনিট |
| Hourly | ১ ঘণ্টা (default) |
| Every 6 Hours | ৬ ঘণ্টা |
| Daily | ২৪ ঘণ্টা |

### যে Woo Item-এর stock sync হয়:
- `published == 1`
- `sync_stock == 1`
- `woo_product_id > 0`
- `variant_publish_mode != "Not Published"`

---

## ৫. Price Sync (দাম সিঙ্ক)

### দুই উপায়ে দাম sync হয়:

#### ক) Real-Time (তাৎক্ষণিক)
```
ERPNext Item Price পরিবর্তন হলে →
  on_item_price_update() [doc_events hook]
        ↓ (শর্ত: enabled + enable_price_sync + সঠিক price list)
  frappe.enqueue → sync_price_for_item_code(item_code)
        ↓
  Template item হলে → সব variant-ও include করে
        ↓
  sync_price == 1 এমন Woo Item → WooCommerce-এ price update
```

#### খ) Scheduled (নির্ধারিত সময়ে)
```
প্রতি ৫ মিনিটে →
  scheduled_price_sync()
        ↓ (price_sync_interval সময় পেরোলে)
  sync_all_prices()
        ↓
  সব published Woo Item (sync_price=1) → 100টা করে batch → WooCommerce price update
```

### Price Sync Interval Options:
| Interval | সময় |
|---|---|
| Hourly | ১ ঘণ্টা |
| Every 6 Hours | ৬ ঘণ্টা |
| Daily | ২৪ ঘণ্টা (default) |

---

## ৬. Sale Price Sync (ছাড়ের দাম সিঙ্ক)

### ৩ উপায়ে Sale Price sync হয়:

#### ক) Real-Time — Pricing Rule পরিবর্তনে
```
ERPNext Pricing Rule save/delete/cancel →
  on_pricing_rule_update() / on_pricing_rule_trash()
        ↓
  sync_pricing_rule_to_woo(rule_name) OR clear_sale_price_for_rule()
        ↓
  WooCommerce-এ sale_price update/clear
```

#### খ) Daily — মেয়াদ শেষ রুল পরিষ্কার
```
প্রতিদিন →
  sync_all_sale_prices_daily()
        ↓
  সব active Pricing Rule চেক করে:
    - মেয়াদ শেষ (valid_upto < আজ)? → sale_price মুছে দাও
    - মেয়াদ আছে? → WooCommerce-এ sync করো
```

---

## ৭. Item Sync — Real-Time Only (পণ্য তথ্য সিঙ্ক)

> [!IMPORTANT]
> Item sync এখন **শুধুমাত্র real-time**। আগে scheduled batch sync (`scheduled_item_sync`) ছিল — সেটা সম্পূর্ণ remove করা হয়েছে।

### কীভাবে হয়:

#### ERPNext Item Master পরিবর্তনে (তাৎক্ষণিক)
```
ERPNext Item save হলে →
  on_item_update() [doc_events hook]
        │
        ├─ settings.enabled == False? → কিছু করে না
        │
        ├─ Variant item? (variant_of আছে?)
        │     └── "With Template" mode?
        │           → Parent template background-এ republish
        │
        ├─ Woo Item আছে + published?
        │     ├── item_name পরিবর্তন → Woo Item update
        │     ├── description পরিবর্তন → Woo Item update
        │     ├── image পরিবর্তন/মুছে গেলে → Woo Item update
        │     └── background-এ publish (image বদলালে sync_images=True)
        │
        └── Woo Item নেই + auto_publish_new_items চালু?
              → নতুন Woo Item তৈরি + publish
```

#### Item Rename হলে
```
ERPNext Item rename →
  on_item_rename()
        ↓
  Published নয়? → Woo Item-এর sku + item_code update
  Published? → SKU পরিবর্তন করে না (live product break হবে) + warning log
```

---

## ৮. Daily Maintenance Jobs (দৈনিক রক্ষণাবেক্ষণ)

| Job | কাজ |
|---|---|
| `reconcile_orders()` | শেষ ২ দিনের WooCommerce order আবার চেক করে missed কোনো order আছে কিনা |
| `prune_sync_logs()` | `log_retention_days` (default ৩০ দিন) এর পুরনো Woo Sync Log মুছে দেয় |
| `sync_all_sale_prices_daily()` | সব Pricing Rule পুনরায় sync করে, মেয়াদ শেষ রুলের sale price মুছে দেয় |

---

## ৯. সম্পূর্ণ Sync Architecture (একনজরে)

```
ERPNext Events (Real-Time)
├── Bin পরিবর্তন        → on_bin_update()                   → Stock sync
├── Item Price পরিবর্তন  → on_item_price_update()            → Price sync
├── Item save            → on_item_update()                   → Item re-publish
├── Item rename          → on_item_rename()                   → SKU drift prevention
├── Pricing Rule         → on_pricing_rule_update()           → Sale price sync
└── Sales Order submit   → on_sales_order_status_change()     → WC order status update

Frappe Scheduler (প্রতি ৫ মিনিটে)
├── scheduled_order_sync()  → auto_sync_orders()   → WC থেকে order pull
├── scheduled_stock_sync()  → sync_all_stock()     → সব stock push
└── scheduled_price_sync()  → sync_all_prices()    → সব price push

Frappe Scheduler (প্রতিদিন)
├── reconcile_orders()                 → Missed order recovery
├── prune_sync_logs()                  → পুরনো log মুছো
└── sync_all_sale_prices_daily()       → Sale price daily check
```

---

## ১০. Woo Settings-এ Toggle করা যায় এমন সব Switch

| Setting Field | Default | নিয়ন্ত্রণ করে |
|---|---|---|
| `enabled` | ✅ | সম্পূর্ণ integration on/off |
| `enable_order_sync` | ✅ | অর্ডার sync |
| `enable_stock_sync` | ✅ | Stock sync |
| `enable_price_sync` | ✅ | Price sync |
| `enable_sale_price_sync` | ✅ | Sale/discount price sync |
| `enable_auto_sync_schedule` | ❌ | Sync time window restriction |
| `auto_publish_new_items` | ❌ | নতুন Item তৈরিতে auto Woo Item + publish |
| `default_warehouse` | (খালি) | শুধু এই warehouse-এর stock sync |

---

*শেষ আপডেট: নভেম্বর ২০২৬ | Woo Prime v1.x (Frappe/ERPNext version-16)*
