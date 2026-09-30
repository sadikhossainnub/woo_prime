# Requirements Document

## Introduction

Convert the WooCommerce product and category fetch operations in the `woo_prime` Frappe v16 app from synchronous web request handlers to background jobs that execute on the `long` queue. The system currently fetches ~2547 products, which takes approximately 4 minutes and exceeds browser request timeouts, resulting in rollback of all changes. The conversion must preserve existing matching logic, auto-linking behavior, and duplicate prevention while adding robust error handling, per-page commits, detailed progress logging, and sequential job execution.

## Glossary

- **Fetch_Job**: A background task queued to Frappe's `long` worker queue that retrieves products or categories from WooCommerce
- **Woo_Item**: ERPNext DocType storing WooCommerce product data (fields: `woo_product_id`, `sku`, `item_code`, `regular_price`, `sale_price`, `published`, `sync_status`)
- **Woo_Category**: ERPNext DocType storing WooCommerce category data in a nested set structure
- **Woo_Sync_Log**: ERPNext DocType recording all sync operations with fields: `sync_type`, `direction`, `status`, `request_data`, `response_data`, `error_message`, `reference_doctype`, `reference_name`
- **ERPNext_Item**: The core Item Master DocType in ERPNext (fields: `item_code`, `item_name`, `item_group`, `stock_uom`, `is_stock_item`)
- **Auto_Create_Missing**: Configuration flag controlling whether to automatically create new ERPNext_Item records for products without matching items
- **Product_Page**: A batch of up to 100 WooCommerce products returned by a single API request
- **Web_Handler**: The whitelisted function that receives the initial user request and enqueues the Fetch_Job
- **Worker_Function**: The actual implementation function that executes asynchronously in the background queue
- **Placeholder_Product**: A WooCommerce product with empty or meaningless data (example: name "Product", slug "product-1303", empty sku, empty price, `purchasable: false`)
- **Fake_SKU**: A generated SKU in the format `WC-{woo_product_id}` created for products without a real SKU
- **Per_Page_Commit**: Database transaction commit performed after processing each Product_Page (every 100 products)
- **Individual_Error_Log**: A separate Woo_Sync_Log entry created for each product that fails processing, containing full exception details

## Requirements

### Requirement 1: Background Job Execution for Product Fetch

**User Story:** As a system administrator, I want product fetching to run as a background job, so that the operation completes successfully without browser timeouts and I can continue using the system

#### Acceptance Criteria

1. WHEN the user calls `fetch_items_from_woocommerce` with `background=True`, THE Fetch_Job SHALL enqueue to the `long` worker queue with a 3600 second timeout
2. WHEN the Fetch_Job is enqueued, THE Web_Handler SHALL return immediately with status "queued" and a message indicating the batch size
3. WHEN the Fetch_Job is enqueued, THE Web_Handler SHALL display a user message using `frappe.msgprint` with indicator "blue" and title "Fetch Queued"
4. THE Worker_Function SHALL execute on the `long` queue using `enqueue_after_commit=True`
5. WHEN multiple users call `fetch_items_from_woocommerce` simultaneously, THE Fetch_Job SHALL queue each job for sequential execution
6. THE Worker_Function SHALL accept parameters `auto_create_missing` and `batch_size` with the same semantics as the current implementation

### Requirement 2: Background Job Execution for Category Fetch

**User Story:** As a system administrator, I want category fetching to run as a background job, so that large category trees sync without browser timeouts

#### Acceptance Criteria

1. WHEN the user calls `sync_categories_from_woo` with `background=True`, THE Fetch_Job SHALL enqueue to the `long` worker queue with a 3600 second timeout
2. WHEN the Fetch_Job is enqueued, THE Web_Handler SHALL return immediately with status "queued" and a message
3. WHEN the Fetch_Job is enqueued, THE Web_Handler SHALL display a user message using `frappe.msgprint` with indicator "blue" and title "Category Sync Queued"
4. THE Worker_Function SHALL execute on the `long` queue using `enqueue_after_commit=True`
5. WHEN multiple users call `sync_categories_from_woo` simultaneously, THE Fetch_Job SHALL queue each job for sequential execution
6. THE Worker_Function SHALL maintain the existing two-pass synchronization algorithm (fetch all, then create/update with parent resolution and tree rebuild)

### Requirement 3: Per-Page Transaction Commits for Products

**User Story:** As a system administrator, I want the system to commit changes after each page of products, so that partial progress is saved if the job fails midway

#### Acceptance Criteria

1. WHEN the Worker_Function completes processing one Product_Page, THE Worker_Function SHALL call `frappe.db.commit()`
2. THE Worker_Function SHALL commit after saving all Woo_Item records and all ERPNext_Item records created for that Product_Page
3. WHEN a Product_Page commit fails, THE Worker_Function SHALL log the failure and continue processing the next Product_Page
4. THE Worker_Function SHALL commit at least once every 100 products processed
5. WHEN the final Product_Page is processed, THE Worker_Function SHALL perform a final commit before returning

### Requirement 4: Individual Product Error Logging

**User Story:** As a system administrator, I want each product processing error logged separately, so that I can identify and fix specific problematic products without re-running the entire fetch

#### Acceptance Criteria

1. WHEN processing a single product raises an exception, THE Worker_Function SHALL create a Woo_Sync_Log entry with status "Failed"
2. THE Individual_Error_Log SHALL contain the full exception traceback in the `error_message` field
3. THE Individual_Error_Log SHALL contain the product ID in the `woo_reference_id` field
4. THE Individual_Error_Log SHALL contain the product SKU in the reference information
5. THE Individual_Error_Log SHALL have `sync_type` set to "Item" and `direction` set to "Incoming"
6. WHEN a product fails processing, THE Worker_Function SHALL continue processing remaining products in the Product_Page
7. WHEN a product fails processing, THE Worker_Function SHALL commit the Individual_Error_Log immediately

### Requirement 5: Detailed Progress Logging for Products

**User Story:** As a system administrator, I want detailed progress logs during product fetch, so that I can monitor the operation and diagnose issues

#### Acceptance Criteria

1. WHEN the Worker_Function starts, THE Worker_Function SHALL log the start time and parameters using the Frappe logger with site-specific logging enabled
2. WHEN the Worker_Function requests a Product_Page, THE Worker_Function SHALL log the page number, API endpoint, and request parameters
3. WHEN the Worker_Function receives a Product_Page, THE Worker_Function SHALL log the HTTP status code and the count of products received
4. WHEN the Worker_Function completes a Product_Page, THE Worker_Function SHALL log the page commit status and cumulative counts (fetched, linked, created, failed)
5. WHEN the Worker_Function completes all pages, THE Worker_Function SHALL log the final summary with total counts and elapsed time
6. WHEN an API request fails, THE Worker_Function SHALL log the HTTP status code, reason, and response body preview (maximum 300 characters)
7. THE Worker_Function SHALL use log level "info" for progress milestones and "error" for failures

### Requirement 6: Detailed Progress Logging for Categories

**User Story:** As a system administrator, I want detailed progress logs during category fetch, so that I can monitor the operation and diagnose hierarchy issues

#### Acceptance Criteria

1. WHEN the Worker_Function starts category fetch, THE Worker_Function SHALL log the start time using the Frappe logger with site-specific logging enabled
2. WHEN the Worker_Function requests a category page, THE Worker_Function SHALL log the page number and request parameters
3. WHEN the Worker_Function receives categories, THE Worker_Function SHALL log the count of categories fetched per page
4. WHEN the Worker_Function completes Pass 1 (fetch all), THE Worker_Function SHALL log the total category count
5. WHEN the Worker_Function begins Pass 2A (create/update records), THE Worker_Function SHALL log the phase start
6. WHEN the Worker_Function begins Pass 2B (parent linking), THE Worker_Function SHALL log the phase start and sort order
7. WHEN the Worker_Function rebuilds the tree, THE Worker_Function SHALL log the tree rebuild operation
8. WHEN the Worker_Function completes, THE Worker_Function SHALL log the final summary with total synced count and elapsed time
9. WHEN an API request fails, THE Worker_Function SHALL log the HTTP status code and response body preview

### Requirement 7: Individual Category Error Logging

**User Story:** As a system administrator, I want each category processing error logged separately, so that I can identify problematic categories without re-running the entire sync

#### Acceptance Criteria

1. WHEN processing a single category raises an exception during Pass 2A or 2B, THE Worker_Function SHALL create a Woo_Sync_Log entry with status "Failed"
2. THE Individual_Error_Log SHALL contain the full exception traceback in the `error_message` field
3. THE Individual_Error_Log SHALL contain the category ID in the `woo_reference_id` field
4. THE Individual_Error_Log SHALL contain the category name in the reference information
5. THE Individual_Error_Log SHALL have `sync_type` set to "Category" and `direction` set to "Incoming"
6. WHEN a category fails processing, THE Worker_Function SHALL continue processing remaining categories
7. WHEN a category fails processing, THE Worker_Function SHALL commit the Individual_Error_Log immediately

### Requirement 8: Preservation of Existing Matching Logic

**User Story:** As a system administrator, I want the background job to use the same matching rules as the current implementation, so that existing integrations continue to work correctly

#### Acceptance Criteria

1. WHEN matching an existing Woo_Item, THE Worker_Function SHALL search first by `woo_product_id`, then by `sku` field, then by primary key name
2. WHEN auto-linking to an ERPNext_Item, THE Worker_Function SHALL search first by `item_code` matching the SKU, then by `name` matching the SKU, then by `item_name` matching the product name
3. WHEN `auto_create_missing` is true and no matching ERPNext_Item exists, THE Worker_Function SHALL create a new ERPNext_Item with `item_code` equal to the SKU
4. THE Worker_Function SHALL use the default Item Group from Woo Settings for newly created ERPNext_Item records
5. THE Worker_Function SHALL set `stock_uom` to "Nos" and `is_stock_item` to 1 for newly created ERPNext_Item records
6. THE Worker_Function SHALL decode HTML entities in product names and descriptions using `html.unescape`
7. WHEN a product has no SKU, THE Worker_Function SHALL generate a Fake_SKU in the format `WC-{woo_product_id}`

### Requirement 9: Preservation of Category Hierarchy Logic

**User Story:** As a system administrator, I want the background job to maintain the same category tree structure as the current implementation, so that parent-child relationships remain correct

#### Acceptance Criteria

1. THE Worker_Function SHALL fetch all categories using pagination with 100 categories per page
2. THE Worker_Function SHALL set `is_group` to 1 for categories that have children, and 0 for leaf categories
3. THE Worker_Function SHALL resolve parent-child relationships by topologically sorting categories by tree depth before linking parent references
4. WHEN a category has a WooCommerce parent ID, THE Worker_Function SHALL link it to the corresponding Woo_Category by matching `woo_category_id`
5. WHEN all categories are created and linked, THE Worker_Function SHALL call `rebuild_tree("Woo Category")` to fix lft/rgt values
6. THE Worker_Function SHALL commit after Pass 2A (basic creation), after Pass 2B (parent linking), and after tree rebuild

### Requirement 10: Duplicate Prevention

**User Story:** As a system administrator, I want to run the fetch operation multiple times without creating duplicate records, so that I can safely retry after failures

#### Acceptance Criteria

1. WHEN a Woo_Item already exists with the same `woo_product_id`, THE Worker_Function SHALL update the existing record instead of creating a new one
2. WHEN a Woo_Item already exists with the same `sku`, THE Worker_Function SHALL update the existing record instead of creating a new one
3. WHEN an ERPNext_Item already exists with the same `item_code`, THE Worker_Function SHALL link to the existing record instead of creating a new one
4. WHEN a Woo_Category already exists with the same `woo_category_id`, THE Worker_Function SHALL update the existing record instead of creating a new one
5. WHEN a Woo_Category already exists with the same `category_name`, THE Worker_Function SHALL update the existing record instead of creating a new one
6. THE Worker_Function SHALL NOT create duplicate Woo_Sync_Log entries for the same Product_Page when retried

### Requirement 11: Backward Compatibility for Direct Calls

**User Story:** As a developer, I want the existing synchronous behavior available for testing and debugging, so that I can run small fetches without background workers

#### Acceptance Criteria

1. WHEN `fetch_items_from_woocommerce` is called with `background=False` or without the `background` parameter, THE Web_Handler SHALL execute the fetch synchronously in the current request context
2. WHEN `fetch_items_from_woocommerce` is called with `background` as a string value, THE Web_Handler SHALL parse "true" and "1" as true, all other values as false
3. WHEN the synchronous execution completes successfully, THE Web_Handler SHALL display a success message using `frappe.msgprint` with indicator "green" and title "Fetch Complete"
4. WHEN `sync_categories_from_woo` is called with `background=False` or without the `background` parameter, THE Web_Handler SHALL execute the sync synchronously
5. THE Web_Handler SHALL return a dictionary with keys "fetched", "linked", and "created" for product operations, or "synced" for category operations

### Requirement 12: Placeholder Product Handling

**User Story:** As a system administrator, I want the system to skip or flag placeholder products, so that my Item Master doesn't fill with meaningless records

#### Acceptance Criteria

1. WHEN a product has an empty or whitespace-only name, THE Worker_Function SHALL skip creating or updating a Woo_Item for that product
2. WHEN a product has a name matching the pattern "Product" with no additional distinguishing text, THE Worker_Function SHALL log a warning
3. WHEN a product has `purchasable: false` and all price fields empty, THE Worker_Function SHALL set `sync_status` to "Not Synced"
4. WHEN `auto_create_missing` is true and a Placeholder_Product is detected, THE Worker_Function SHALL NOT create an ERPNext_Item
5. THE Worker_Function SHALL log each skipped Placeholder_Product with the WooCommerce product ID and reason

### Requirement 13: Success Logging for Product Fetch

**User Story:** As a system administrator, I want successful page fetches logged to Woo Sync Log, so that I have a complete audit trail of all API interactions

#### Acceptance Criteria

1. WHEN a Product_Page is successfully fetched from WooCommerce, THE Worker_Function SHALL create a Woo_Sync_Log entry with status "Success"
2. THE Woo_Sync_Log entry SHALL contain the request parameters in the `request_data` field as JSON
3. THE Woo_Sync_Log entry SHALL contain the HTTP status code and response body preview (first 5000 characters) in the `response_data` field as JSON
4. THE Woo_Sync_Log entry SHALL have `sync_type` set to "Item" and `direction` set to "Incoming"
5. THE Worker_Function SHALL create one success log per Product_Page, not per individual product

### Requirement 14: Success Logging for Category Fetch

**User Story:** As a system administrator, I want successful category page fetches logged to Woo Sync Log, so that I have a complete audit trail

#### Acceptance Criteria

1. WHEN a category page is successfully fetched from WooCommerce, THE Worker_Function SHALL create a Woo_Sync_Log entry with status "Success"
2. THE Woo_Sync_Log entry SHALL contain the request parameters in the `request_data` field as JSON
3. THE Woo_Sync_Log entry SHALL contain the HTTP status code and response body preview in the `response_data` field as JSON
4. THE Woo_Sync_Log entry SHALL have `sync_type` set to "Category" and `direction` set to "Incoming"
5. THE Worker_Function SHALL create one success log per category page fetch

### Requirement 15: API Failure Handling

**User Story:** As a system administrator, I want the system to gracefully handle WooCommerce API failures, so that temporary network issues don't crash the entire job

#### Acceptance Criteria

1. WHEN the WooCommerce API returns an HTTP status code other than 200, THE Worker_Function SHALL create a Woo_Sync_Log entry with status "Failed"
2. THE Woo_Sync_Log entry SHALL contain the HTTP status code, reason phrase, and response body preview (first 300 characters) in the `error_message` field
3. WHEN the WooCommerce API returns a 4xx client error, THE Worker_Function SHALL stop the fetch operation and raise an exception
4. WHEN the WooCommerce API returns a 5xx server error on the first page, THE Worker_Function SHALL stop the fetch operation and raise an exception
5. WHEN the WooCommerce API returns a 5xx server error on a subsequent page, THE Worker_Function SHALL log the error and stop further pagination
6. THE Worker_Function SHALL include the failed request details in both the logger output and the Woo_Sync_Log entry

### Requirement 16: Final Summary Notification

**User Story:** As a system administrator, I want a summary notification when the background job completes, so that I know the results without checking logs

#### Acceptance Criteria

1. WHEN the Worker_Function completes successfully, THE Worker_Function SHALL create a Woo_Sync_Log entry with status "Success" and a summary message
2. THE summary message SHALL include the total count of products fetched or categories synced
3. THE summary message SHALL include the count of items auto-linked to ERPNext_Item records
4. THE summary message SHALL include the count of new ERPNext_Item records created
5. THE summary message SHALL include the count of products that failed processing
6. THE summary message SHALL include the elapsed time in seconds
7. THE summary Woo_Sync_Log entry SHALL have `sync_type` set to "Item" or "Category" depending on the operation
