<?php
/**
 * Woo Prime Settings Page (Enhanced v2.0)
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Settings {

	public static function init() {
		add_action( 'admin_menu', array( __CLASS__, 'add_admin_menu' ) );
		add_action( 'admin_init', array( __CLASS__, 'register_settings' ) );
		add_action( 'wp_before_admin_bar_render', array( __CLASS__, 'add_admin_bar_badge' ) );

		// AJAX handlers
		add_action( 'wp_ajax_woo_prime_test_connection', array( __CLASS__, 'ajax_test_connection' ) );
		add_action( 'wp_ajax_woo_prime_clear_cache', array( __CLASS__, 'ajax_clear_cache' ) );
		add_action( 'wp_ajax_woo_prime_clear_logs', array( __CLASS__, 'ajax_clear_logs' ) );
		add_action( 'wp_ajax_woo_prime_get_logs', array( __CLASS__, 'ajax_get_logs' ) );
		add_action( 'wp_ajax_woo_prime_list_erpnext_items', array( __CLASS__, 'ajax_list_erpnext_items' ) );
		add_action( 'wp_ajax_woo_prime_import_selected_items', array( __CLASS__, 'ajax_import_selected_items' ) );
	}

	public static function add_admin_menu() {
		if ( class_exists( 'WooCommerce' ) ) {
			add_submenu_page(
				'woocommerce',
				__( 'Woo Prime Settings', 'woo-prime-connector' ),
				__( 'Woo Prime ERPNext', 'woo-prime-connector' ),
				'manage_options',
				'woo-prime-settings',
				array( __CLASS__, 'render_settings_page' )
			);
		}

		add_options_page(
			__( 'Woo Prime Settings', 'woo-prime-connector' ),
			__( 'Woo Prime ERPNext', 'woo-prime-connector' ),
			'manage_options',
			'woo-prime-settings',
			array( __CLASS__, 'render_settings_page' )
		);
	}

	public static function register_settings() {
		register_setting( 'woo_prime_options_group', 'woo_prime_erpnext_url' );
		register_setting( 'woo_prime_options_group', 'woo_prime_api_key' );
		register_setting( 'woo_prime_options_group', 'woo_prime_api_secret' );
		register_setting( 'woo_prime_options_group', 'woo_prime_enable_pricing_rules' );
		register_setting( 'woo_prime_options_group', 'woo_prime_enable_loyalty' );
		register_setting( 'woo_prime_options_group', 'woo_prime_cache_ttl' );
		register_setting( 'woo_prime_options_group', 'woo_prime_enable_logging' );
	}

	public static function add_admin_bar_badge() {
		global $wp_admin_bar;

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			return;
		}

		$is_connected = Woo_Prime_Cache::get( 'connection_status' );
		if ( false === $is_connected ) {
			// Quick ping check
			$response     = wp_remote_get( $erpnext_url . '/api/method/frappe.handler.ping', array( 'timeout' => 3 ) );
			$is_connected = ( ! is_wp_error( $response ) && 200 === wp_remote_retrieve_response_code( $response ) ) ? 1 : 0;
			Woo_Prime_Cache::set( 'connection_status', $is_connected, 300 );
		}

		$status_icon = $is_connected ? '🟢' : '🔴';
		$status_text = $is_connected ? __( 'ERPNext Connected', 'woo-prime-connector' ) : __( 'ERPNext Disconnected', 'woo-prime-connector' );

		$wp_admin_bar->add_node( array(
			'id'    => 'woo_prime_status',
			'title' => sprintf( '%s %s', $status_icon, $status_text ),
			'href'  => admin_url( 'admin.php?page=woo-prime-settings' ),
		) );
	}

	public static function ajax_test_connection() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			wp_send_json_error( __( 'ERPNext URL is empty.', 'woo-prime-connector' ) );
		}

		$api_key    = get_option( 'woo_prime_api_key' );
		$api_secret = get_option( 'woo_prime_api_secret' );
		$headers    = array( 'Content-Type' => 'application/json' );

		if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
			$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
		}

		$response = wp_remote_get( $erpnext_url . '/api/method/woo_prime.api.dashboard.get_dashboard_stats', array(
			'headers' => $headers,
			'timeout' => 10,
		) );

		if ( is_wp_error( $response ) ) {
			Woo_Prime_Logger::log( 'error', 'Connection test failed', array( 'error' => $response->get_error_message() ) );
			wp_send_json_error( $response->get_error_message() );
		}

		$code = wp_remote_retrieve_response_code( $response );
		if ( 200 === $code ) {
			Woo_Prime_Cache::set( 'connection_status', 1, 300 );
			wp_send_json_success( __( '✅ Connection Successful! ERPNext is active and responding.', 'woo-prime-connector' ) );
		} else {
			// Fallback ping check to verify ERPNext server reachability
			$ping_url      = $erpnext_url . '/api/method/frappe.handler.ping';
			$ping_response = wp_remote_get( $ping_url, array(
				'headers' => $headers,
				'timeout' => 6,
			) );

			if ( ! is_wp_error( $ping_response ) && 200 === wp_remote_retrieve_response_code( $ping_response ) ) {
				Woo_Prime_Cache::set( 'connection_status', 1, 300 );
				if ( 417 === $code ) {
					wp_send_json_success( __( '✅ Connection Successful! ERPNext site is active and responding via Ping API. (Note: Please run `bench restart` on your ERPNext server to enable live dashboard stats).', 'woo-prime-connector' ) );
				} else {
					wp_send_json_success( __( '✅ Connection Successful! ERPNext site is active and responding.', 'woo-prime-connector' ) );
				}
				return;
			}

			Woo_Prime_Cache::set( 'connection_status', 0, 300 );
			$msg = sprintf( __( '❌ Connection Failed! HTTP Status Code: %d', 'woo-prime-connector' ), $code );
			if ( 417 === $code ) {
				$msg .= ' ' . __( '(ERPNext server returned 417 Expectation Failed. Run `bench restart` or `bench migrate` on ERPNext server).', 'woo-prime-connector' );
			}
			Woo_Prime_Logger::log( 'error', $msg );
			wp_send_json_error( $msg );
		}
	}

	public static function ajax_clear_cache() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );
		Woo_Prime_Cache::clear_all();
		wp_send_json_success( __( 'Cache cleared successfully!', 'woo-prime-connector' ) );
	}

	public static function ajax_clear_logs() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );
		Woo_Prime_Logger::clear_logs();
		wp_send_json_success( __( 'Log file cleared successfully!', 'woo-prime-connector' ) );
	}

	public static function ajax_get_logs() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );
		$logs = Woo_Prime_Logger::get_logs();
		wp_send_json_success( $logs );
	}

	public static function ajax_list_erpnext_items() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			wp_send_json_error( __( 'ERPNext URL is empty.', 'woo-prime-connector' ) );
		}

		$api_key    = get_option( 'woo_prime_api_key' );
		$api_secret = get_option( 'woo_prime_api_secret' );
		$headers    = array( 'Content-Type' => 'application/json' );

		if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
			$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
		}

		$search   = isset( $_POST['search'] ) ? sanitize_text_field( $_POST['search'] ) : '';
		$endpoint = $erpnext_url . '/api/method/woo_prime.api.products.get_items_for_sync?limit=200';
		if ( ! empty( $search ) ) {
			$endpoint .= '&search=' . rawurlencode( $search );
		}

		$response = wp_remote_get( $endpoint, array(
			'headers' => $headers,
			'timeout' => 20,
		) );

		if ( is_wp_error( $response ) ) {
			wp_send_json_error( __( 'Failed to fetch items from ERPNext: ', 'woo-prime-connector' ) . $response->get_error_message() );
		}

		$code = wp_remote_retrieve_response_code( $response );
		if ( 200 !== $code ) {
			wp_send_json_error( sprintf( __( 'ERPNext API status error: %d', 'woo-prime-connector' ), $code ) );
		}

		$body  = wp_remote_retrieve_body( $response );
		$data  = json_decode( $body, true );
		$items = isset( $data['message']['items'] ) ? $data['message']['items'] : ( isset( $data['items'] ) ? $data['items'] : array() );

		// Enrich each item with WooCommerce exists check
		foreach ( $items as &$item ) {
			$sku = isset( $item['sku'] ) ? trim( $item['sku'] ) : '';
			$product_id = ! empty( $sku ) && function_exists( 'wc_get_product_id_by_sku' ) ? wc_get_product_id_by_sku( $sku ) : 0;
			$item['is_in_wc'] = ( $product_id > 0 );
			$item['wc_product_id'] = $product_id;
		}

		wp_send_json_success( $items );
	}

	public static function ajax_import_selected_items() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );

		$selected_skus = isset( $_POST['selected_skus'] ) ? array_map( 'sanitize_text_field', (array) $_POST['selected_skus'] ) : array();

		if ( empty( $selected_skus ) ) {
			wp_send_json_error( __( 'No items were selected for import.', 'woo-prime-connector' ) );
		}

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			wp_send_json_error( __( 'ERPNext URL is empty.', 'woo-prime-connector' ) );
		}

		$api_key    = get_option( 'woo_prime_api_key' );
		$api_secret = get_option( 'woo_prime_api_secret' );
		$headers    = array( 'Content-Type' => 'application/json' );

		if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
			$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
		}

		// Query ERPNext API specifically for selected item codes
		$endpoint = $erpnext_url . '/api/method/woo_prime.api.products.get_items_for_sync?limit=500&item_codes=' . rawurlencode( json_encode( $selected_skus ) );
		$response = wp_remote_get( $endpoint, array(
			'headers' => $headers,
			'timeout' => 30,
		) );

		if ( is_wp_error( $response ) ) {
			wp_send_json_error( __( 'Failed to connect to ERPNext: ', 'woo-prime-connector' ) . $response->get_error_message() );
		}

		$code = wp_remote_retrieve_response_code( $response );
		if ( 200 !== $code ) {
			wp_send_json_error( sprintf( __( 'ERPNext error status code: %d', 'woo-prime-connector' ), $code ) );
		}

		$body  = wp_remote_retrieve_body( $response );
		$data  = json_decode( $body, true );
		$items = isset( $data['message']['items'] ) ? $data['message']['items'] : ( isset( $data['items'] ) ? $data['items'] : array() );

		if ( empty( $items ) ) {
			wp_send_json_error( __( 'Could not retrieve details for selected items from ERPNext.', 'woo-prime-connector' ) );
		}

		if ( ! class_exists( 'WooCommerce' ) ) {
			wp_send_json_error( __( 'WooCommerce plugin is not active.', 'woo-prime-connector' ) );
		}

		$created_count = 0;
		$updated_count = 0;

		foreach ( $items as $item ) {
			$sku   = isset( $item['sku'] ) ? trim( $item['sku'] ) : '';
			$name  = isset( $item['item_name'] ) ? html_entity_decode( $item['item_name'], ENT_QUOTES | ENT_HTML5, 'UTF-8' ) : $sku;
			$price = isset( $item['price'] ) ? floatval( $item['price'] ) : 0;
			$stock = isset( $item['stock_quantity'] ) ? intval( $item['stock_quantity'] ) : 0;
			$desc  = isset( $item['description'] ) ? html_entity_decode( $item['description'], ENT_QUOTES | ENT_HTML5, 'UTF-8' ) : '';

			if ( empty( $sku ) ) {
				continue;
			}

			$product_id = wc_get_product_id_by_sku( $sku );

			if ( $product_id ) {
				$product = wc_get_product( $product_id );
				$updated_count++;
			} else {
				$product = new WC_Product_Simple();
				$product->set_sku( $sku );
				$created_count++;
			}

			$product->set_name( $name );
			$product->set_regular_price( $price );
			$product->set_manage_stock( true );
			$product->set_stock_quantity( $stock );
			$product->set_status( 'publish' );
			if ( ! empty( $desc ) ) {
				$product->set_description( $desc );
			}

			$product->save();
		}

		$msg = sprintf(
			__( '✅ Selected Items Import Complete! Created: %d new products, Updated: %d existing products in WooCommerce.', 'woo-prime-connector' ),
			$created_count,
			$updated_count
		);

		Woo_Prime_Logger::log( 'info', $msg );
		wp_send_json_success( $msg );
	}

	public static function render_settings_page() {
		?>
		<div class="wrap woo-prime-settings-wrap">
			<h1><?php esc_html_e( 'Woo Prime — ERPNext Integration Settings v2.0', 'woo-prime-connector' ); ?></h1>
			<form method="post" action="options.php">
				<?php
				settings_fields( 'woo_prime_options_group' );
				do_settings_sections( 'woo_prime_options_group' );
				?>
				<table class="form-table">
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'ERPNext Site URL', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="url" name="woo_prime_erpnext_url" value="<?php echo esc_attr( get_option( 'woo_prime_erpnext_url', 'http://127.0.0.1:8000' ) ); ?>" class="regular-text" placeholder="https://erp.yourdomain.com" required />
							<p class="description"><?php esc_html_e( 'Enter your ERPNext instance URL without trailing slash.', 'woo-prime-connector' ); ?></p>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'API Key (Optional)', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="text" name="woo_prime_api_key" value="<?php echo esc_attr( get_option( 'woo_prime_api_key', '' ) ); ?>" class="regular-text" placeholder="e.g. 5d8a9b2c1..." />
							<p class="description"><?php esc_html_e( 'ERPNext API Key for authenticated API calls.', 'woo-prime-connector' ); ?></p>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'API Secret (Optional)', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="password" name="woo_prime_api_secret" value="<?php echo esc_attr( get_option( 'woo_prime_api_secret', '' ) ); ?>" class="regular-text" />
							<p class="description"><?php esc_html_e( 'ERPNext API Secret.', 'woo-prime-connector' ); ?></p>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'Enable Pricing Rules', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="checkbox" name="woo_prime_enable_pricing_rules" value="1" <?php checked( 1, get_option( 'woo_prime_enable_pricing_rules', 1 ) ); ?> />
							<label><?php esc_html_e( 'Evaluate ERPNext Pricing Rules in WooCommerce Cart in Real-Time (Per-Item Breakdown)', 'woo-prime-connector' ); ?></label>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'Enable Loyalty Points', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="checkbox" name="woo_prime_enable_loyalty" value="1" <?php checked( 1, get_option( 'woo_prime_enable_loyalty', 1 ) ); ?> />
							<label><?php esc_html_e( 'Show Customer Loyalty Point Balance & Allow Instant Redemption at Checkout (AJAX)', 'woo-prime-connector' ); ?></label>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'Cache Duration (Minutes)', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="number" name="woo_prime_cache_ttl" value="<?php echo esc_attr( get_option( 'woo_prime_cache_ttl', 5 ) ); ?>" class="small-text" min="1" max="60" />
							<p class="description"><?php esc_html_e( 'Duration to cache API responses (pricing rules, points balance) to improve page speed.', 'woo-prime-connector' ); ?></p>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'Enable Debug Logging', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="checkbox" name="woo_prime_enable_logging" value="1" <?php checked( 1, get_option( 'woo_prime_enable_logging', 1 ) ); ?> />
							<label><?php esc_html_e( 'Log ERPNext API requests and errors to log file for troubleshooting', 'woo-prime-connector' ); ?></label>
						</td>
					</tr>
				</table>

				<div class="woo-prime-actions" style="margin-top: 20px; padding: 15px; background: #fff; border: 1px solid #ccc; border-radius: 4px;">
					<h3><?php esc_html_e( 'Tools & Selective Item Importer', 'woo-prime-connector' ); ?></h3>
					<button type="button" id="woo-prime-test-conn-btn" class="button button-primary"><?php esc_html_e( 'Test Connection to ERPNext', 'woo-prime-connector' ); ?></button>
					<button type="button" id="woo-prime-open-item-modal-btn" class="button button-primary" style="background:#00a32a; border-color:#00a32a; margin-left: 5px;"><?php esc_html_e( '📦 Select & Import Items from ERPNext', 'woo-prime-connector' ); ?></button>
					<button type="button" id="woo-prime-clear-cache-btn" class="button button-secondary"><?php esc_html_e( 'Clear Cache', 'woo-prime-connector' ); ?></button>
					<button type="button" id="woo-prime-view-logs-btn" class="button button-secondary"><?php esc_html_e( 'View Error Logs', 'woo-prime-connector' ); ?></button>
					<div id="woo-prime-action-status" style="margin-top: 10px; font-weight: bold; min-height: 20px;"></div>
				</div>

				<?php submit_button(); ?>
			</form>
		</div>

		<!-- ERPNext Item Selector & Importer Modal -->
		<div id="woo-prime-item-select-modal" style="display:none; position:fixed; z-index:99999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6);">
			<div style="background:#fff; margin:3% auto; padding:25px; width:80%; max-height:88vh; overflow-y:auto; border-radius:6px; position:relative; box-shadow:0 5px 20px rgba(0,0,0,0.3);">
				<div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #ddd; padding-bottom:12px; margin-bottom:15px;">
					<h2 style="margin:0;"><?php esc_html_e( 'ERPNext Live Item Importer (Select & Import)', 'woo-prime-connector' ); ?></h2>
					<button type="button" id="item-modal-close-top" class="button button-secondary">✕</button>
				</div>

				<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px; background:#f6f7f7; padding:10px; border-radius:4px;">
					<div style="display:flex; align-items:center; gap:10px;">
						<input type="text" id="item-search-input" placeholder="Search by Item Name or SKU..." class="regular-text" style="width:260px;" />
						<button type="button" id="item-search-btn" class="button button-secondary"><?php esc_html_e( 'Search', 'woo-prime-connector' ); ?></button>
						<button type="button" id="item-reload-btn" class="button button-secondary"><?php esc_html_e( '🔄 Reload All', 'woo-prime-connector' ); ?></button>
					</div>
					<div style="display:flex; align-items:center; gap:15px;">
						<div class="button-group" style="display:inline-flex;">
							<button type="button" id="view-mode-grid-btn" class="button button-primary active-view-btn">🔲 <?php esc_html_e( 'Grid View', 'woo-prime-connector' ); ?></button>
							<button type="button" id="view-mode-list-btn" class="button button-secondary">☰ <?php esc_html_e( 'List View', 'woo-prime-connector' ); ?></button>
						</div>
						<label style="font-weight:bold; cursor:pointer;"><input type="checkbox" id="item-select-all-cb" /> <?php esc_html_e( 'Select All Visible', 'woo-prime-connector' ); ?></label>
					</div>
				</div>

				<div style="max-height:440px; overflow-y:auto; border:1px solid #c3c4c7; border-radius:4px; margin-bottom:15px; padding:12px; background:#fcfcfc;">
					<!-- Grid View Container -->
					<div id="erpnext-items-grid-wrap" style="display:grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap:15px;">
						<div style="grid-column:1/-1; text-align:center; padding:30px; color:#646970;"><?php esc_html_e( 'Click "Select & Import Items" to load live items from ERPNext...', 'woo-prime-connector' ); ?></div>
					</div>

					<!-- List View Container -->
					<div id="erpnext-items-list-wrap" style="display:none;">
						<table class="wp-list-table widefat fixed striped" style="border-collapse:collapse; width:100%;">
							<thead>
								<tr>
									<th style="width:50px; text-align:center;"><?php esc_html_e( 'Select', 'woo-prime-connector' ); ?></th>
									<th style="width:60px;"><?php esc_html_e( 'Image', 'woo-prime-connector' ); ?></th>
									<th style="width:140px;"><?php esc_html_e( 'SKU / Code', 'woo-prime-connector' ); ?></th>
									<th><?php esc_html_e( 'Item Name', 'woo-prime-connector' ); ?></th>
									<th style="width:100px;"><?php esc_html_e( 'Price', 'woo-prime-connector' ); ?></th>
									<th style="width:90px;"><?php esc_html_e( 'Stock Qty', 'woo-prime-connector' ); ?></th>
									<th style="width:130px; text-align:center;"><?php esc_html_e( 'WC Status', 'woo-prime-connector' ); ?></th>
								</tr>
							</thead>
							<tbody id="erpnext-items-tbody">
							</tbody>
						</table>
					</div>
				</div>

				<div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid #ddd; padding-top:15px;">
					<span id="selected-items-count" style="font-weight:bold; font-size:14px; color:#2271b1;"><?php esc_html_e( 'Selected: 0 items', 'woo-prime-connector' ); ?></span>
					<div>
						<button type="button" id="woo-prime-import-selected-btn" class="button button-primary" style="background:#00a32a; border-color:#00a32a; font-weight:bold; padding:4px 15px;" disabled>📥 <?php esc_html_e( 'Import Selected Items to WooCommerce', 'woo-prime-connector' ); ?></button>
						<button type="button" id="item-modal-close-btn" class="button button-secondary" style="margin-left:8px;"><?php esc_html_e( 'Close', 'woo-prime-connector' ); ?></button>
					</div>
				</div>
			</div>
		</div>

		<!-- Log Modal -->
		<div id="woo-prime-log-modal" style="display:none; position:fixed; z-index:99999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.5);">
			<div style="background:#fff; margin:5% auto; padding:20px; width:70%; max-height:80vh; overflow-y:auto; border-radius:6px; position:relative;">
				<h2><?php esc_html_e( 'Woo Prime Error Logs', 'woo-prime-connector' ); ?></h2>
				<pre id="woo-prime-log-content" style="background:#222; color:#0f0; padding:15px; max-height:400px; overflow-y:auto; border-radius:4px; font-size:12px;"></pre>
				<button type="button" id="woo-prime-clear-logs-btn" class="button button-link-delete"><?php esc_html_e( 'Clear Log File', 'woo-prime-connector' ); ?></button>
				<button type="button" id="woo-prime-close-log-modal" class="button button-secondary" style="float:right;"><?php esc_html_e( 'Close', 'woo-prime-connector' ); ?></button>
			</div>
		</div>
		<?php
	}
}
