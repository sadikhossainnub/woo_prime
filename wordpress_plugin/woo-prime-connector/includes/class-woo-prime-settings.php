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
	}

	public static function add_admin_menu() {
		add_submenu_page(
			'woocommerce',
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
			Woo_Prime_Cache::set( 'connection_status', 0, 300 );
			$msg = sprintf( __( '❌ Connection Failed! HTTP Status Code: %d', 'woo-prime-connector' ), $code );
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
					<h3><?php esc_html_e( 'Tools & Connection Test', 'woo-prime-connector' ); ?></h3>
					<button type="button" id="woo-prime-test-conn-btn" class="button button-primary"><?php esc_html_e( 'Test Connection to ERPNext', 'woo-prime-connector' ); ?></button>
					<button type="button" id="woo-prime-clear-cache-btn" class="button button-secondary"><?php esc_html_e( 'Clear Cache', 'woo-prime-connector' ); ?></button>
					<button type="button" id="woo-prime-view-logs-btn" class="button button-secondary"><?php esc_html_e( 'View Error Logs', 'woo-prime-connector' ); ?></button>
					<span id="woo-prime-action-status" style="margin-left: 10px; font-weight: bold;"></span>
				</div>

				<?php submit_button(); ?>
			</form>
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
