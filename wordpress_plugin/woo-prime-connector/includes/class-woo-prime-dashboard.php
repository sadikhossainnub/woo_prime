<?php
/**
 * Woo Prime Dashboard & Transaction Log Viewer
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Dashboard {

	public static function init() {
		add_action( 'wp_dashboard_setup', array( __CLASS__, 'add_dashboard_widget' ) );
		add_action( 'admin_menu', array( __CLASS__, 'add_admin_menu' ) );
		add_action( 'wp_ajax_woo_prime_fetch_transactions', array( __CLASS__, 'ajax_fetch_transactions' ) );
	}

	public static function add_admin_menu() {
		if ( class_exists( 'WooCommerce' ) ) {
			add_submenu_page(
				'woocommerce',
				__( 'Woo Prime Sync Dashboard', 'woo-prime-connector' ),
				__( 'Sync Dashboard', 'woo-prime-connector' ),
				'manage_options',
				'woo-prime-dashboard',
				array( __CLASS__, 'render_dashboard_page' )
			);
		}
	}

	public static function add_dashboard_widget() {
		wp_add_dashboard_widget(
			'woo_prime_dashboard_widget',
			__( 'Woo Prime — ERPNext Integration', 'woo-prime-connector' ),
			array( __CLASS__, 'render_widget' )
		);
	}

	public static function ajax_fetch_transactions() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			wp_send_json_error( __( 'ERPNext URL is not configured.', 'woo-prime-connector' ) );
		}

		$api_key    = get_option( 'woo_prime_api_key' );
		$api_secret = get_option( 'woo_prime_api_secret' );
		$headers    = array( 'Content-Type' => 'application/json' );

		if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
			$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
		}

		$response = wp_remote_get(
			$erpnext_url . '/api/method/woo_prime.api.dashboard.get_dashboard_stats',
			array( 'headers' => $headers, 'timeout' => 12 )
		);

		if ( is_wp_error( $response ) ) {
			wp_send_json_error( $response->get_error_message() );
		}

		$code = wp_remote_retrieve_response_code( $response );
		if ( 200 !== $code ) {
			wp_send_json_error( sprintf( __( 'HTTP Error Status: %d', 'woo-prime-connector' ), $code ) );
		}

		$body = wp_remote_retrieve_body( $response );
		$data = json_decode( $body, true );
		$stats = isset( $data['message'] ) ? $data['message'] : null;

		if ( ! $stats ) {
			wp_send_json_error( __( 'Could not parse response from ERPNext API.', 'woo-prime-connector' ) );
		}

		wp_send_json_success( $stats );
	}

	public static function render_widget() {
		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			echo '<p>' . esc_html__( 'ERPNext URL is not configured. Go to WooCommerce → Woo Prime ERPNext to set up.', 'woo-prime-connector' ) . '</p>';
			return;
		}

		$cache_key = 'dashboard_stats';
		$stats     = Woo_Prime_Cache::get( $cache_key );

		if ( false === $stats ) {
			$api_key    = get_option( 'woo_prime_api_key' );
			$api_secret = get_option( 'woo_prime_api_secret' );
			$headers    = array( 'Content-Type' => 'application/json' );

			if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
				$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
			}

			$response = wp_remote_get(
				$erpnext_url . '/api/method/woo_prime.api.dashboard.get_dashboard_stats',
				array( 'headers' => $headers, 'timeout' => 8 )
			);

			if ( ! is_wp_error( $response ) && 200 === wp_remote_retrieve_response_code( $response ) ) {
				$body  = wp_remote_retrieve_body( $response );
				$data  = json_decode( $body, true );
				$stats = isset( $data['message'] ) ? $data['message'] : null;
				if ( $stats ) {
					Woo_Prime_Cache::set( $cache_key, $stats, 600 );
				}
			}
		}

		$status_val = is_array( $stats ) && isset( $stats['status'] ) ? $stats['status'] : '';

		if ( ! $stats || 'success' !== $status_val ) {
			echo '<p style="color:#d63638;">❌ ' . esc_html__( 'Could not fetch live ERPNext statistics. Check ERPNext URL & connection in Woo Prime Settings.', 'woo-prime-connector' ) . '</p>';
			return;
		}

		?>
		<div class="woo-prime-dashboard-widget">
			<p><strong>Status:</strong> <span style="color:#00a32a; font-weight:600;">🟢 ERPNext Connected</span></p>
			<div style="display:flex; justify-content:space-between; margin-bottom:12px;">
				<div style="background:#f0f6fc; padding:10px; border-radius:4px; text-align:center; flex:1; margin-right:6px;">
					<h3 style="margin:0; font-size:20px; color:#2271b1;"><?php echo esc_html( isset( $stats['today_orders_count'] ) ? $stats['today_orders_count'] : 0 ); ?></h3>
					<small><?php esc_html_e( 'Orders Synced Today', 'woo-prime-connector' ); ?></small>
				</div>
				<div style="background:#f0f6fc; padding:10px; border-radius:4px; text-align:center; flex:1; margin-left:6px;">
					<h3 style="margin:0; font-size:20px; color:#2271b1;"><?php echo esc_html( isset( $stats['week_orders_count'] ) ? $stats['week_orders_count'] : 0 ); ?></h3>
					<small><?php esc_html_e( 'Orders Last 7 Days', 'woo-prime-connector' ); ?></small>
				</div>
			</div>
			<?php if ( ! empty( $stats['last_synced_order'] ) ) : ?>
				<p style="font-size:12px; margin-bottom:5px;">
					<strong>Last Order Synced:</strong> WC-<?php echo esc_html( $stats['last_synced_order']['woo_order_id'] ); ?> → 
					<code><?php echo esc_html( $stats['last_synced_order']['name'] ); ?></code>
				</p>
			<?php endif; ?>
			<p style="margin-top:10px;">
				<a href="<?php echo esc_url( admin_url( 'admin.php?page=woo-prime-dashboard' ) ); ?>" class="button button-primary"><?php esc_html_e( 'View Sync Dashboard', 'woo-prime-connector' ); ?></a>
			</p>
		</div>
		<?php
	}

	public static function render_dashboard_page() {
		?>
		<div class="wrap woo-prime-dashboard-page">
			<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
				<h1 style="margin:0;"><?php esc_html_e( 'Woo Prime — API Transaction & Sync Dashboard', 'woo-prime-connector' ); ?></h1>
				<button type="button" id="woo-prime-refresh-dash-btn" class="button button-primary" style="background:#2271b1;">🔄 <?php esc_html_e( 'Refresh Live Data', 'woo-prime-connector' ); ?></button>
			</div>

			<!-- Stat Cards Row -->
			<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:15px; margin-bottom:25px;">
				<div style="background:#fff; border:1px solid #c3c4c7; border-left:4px solid #2271b1; padding:15px; border-radius:4px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
					<span style="font-size:12px; text-transform:uppercase; color:#646970; font-weight:600;"><?php esc_html_e( 'Today\'s Orders', 'woo-prime-connector' ); ?></span>
					<h2 id="dash-today-orders" style="margin:5px 0 0 0; font-size:28px; color:#1d2327;">--</h2>
				</div>
				<div style="background:#fff; border:1px solid #c3c4c7; border-left:4px solid #135e96; padding:15px; border-radius:4px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
					<span style="font-size:12px; text-transform:uppercase; color:#646970; font-weight:600;"><?php esc_html_e( 'Last 7 Days Orders', 'woo-prime-connector' ); ?></span>
					<h2 id="dash-week-orders" style="margin:5px 0 0 0; font-size:28px; color:#1d2327;">--</h2>
				</div>
				<div style="background:#fff; border:1px solid #c3c4c7; border-left:4px solid #00a32a; padding:15px; border-radius:4px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
					<span style="font-size:12px; text-transform:uppercase; color:#646970; font-weight:600;"><?php esc_html_e( 'Successful Syncs', 'woo-prime-connector' ); ?></span>
					<h2 id="dash-success-count" style="margin:5px 0 0 0; font-size:28px; color:#00a32a;">--</h2>
				</div>
				<div style="background:#fff; border:1px solid #c3c4c7; border-left:4px solid #d63638; padding:15px; border-radius:4px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
					<span style="font-size:12px; text-transform:uppercase; color:#646970; font-weight:600;"><?php esc_html_e( 'Failed Syncs', 'woo-prime-connector' ); ?></span>
					<h2 id="dash-failed-count" style="margin:5px 0 0 0; font-size:28px; color:#d63638;">--</h2>
				</div>
			</div>

			<!-- Recent Sync Transactions Table -->
			<div style="background:#fff; border:1px solid #c3c4c7; border-radius:4px; padding:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
				<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
					<h2 style="margin:0; font-size:18px;"><?php esc_html_e( 'Recent API Sync Transactions', 'woo-prime-connector' ); ?></h2>
					<span id="dash-status-msg" style="font-weight:bold; font-size:13px;"></span>
				</div>

				<table class="wp-list-table widefat fixed striped" style="border-collapse:collapse; width:100%;">
					<thead>
						<tr>
							<th style="width:120px;"><?php esc_html_e( 'Log ID', 'woo-prime-connector' ); ?></th>
							<th style="width:100px;"><?php esc_html_e( 'Type', 'woo-prime-connector' ); ?></th>
							<th style="width:100px;"><?php esc_html_e( 'Direction', 'woo-prime-connector' ); ?></th>
							<th style="width:90px;"><?php esc_html_e( 'Status', 'woo-prime-connector' ); ?></th>
							<th><?php esc_html_e( 'Reference', 'woo-prime-connector' ); ?></th>
							<th style="width:120px;"><?php esc_html_e( 'Woo Ref ID', 'woo-prime-connector' ); ?></th>
							<th style="width:160px;"><?php esc_html_e( 'Timestamp', 'woo-prime-connector' ); ?></th>
							<th style="width:100px; text-align:center;"><?php esc_html_e( 'Payload', 'woo-prime-connector' ); ?></th>
						</tr>
					</thead>
					<tbody id="dash-transactions-tbody">
						<tr>
							<td colspan="8" style="text-align:center; padding:20px; color:#646970;"><?php esc_html_e( 'Loading live API transactions from ERPNext...', 'woo-prime-connector' ); ?></td>
						</tr>
					</tbody>
				</table>
			</div>
		</div>

		<!-- Transaction Payload Detail Modal -->
		<div id="woo-prime-tx-modal" style="display:none; position:fixed; z-index:99999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6);">
			<div style="background:#fff; margin:4% auto; padding:25px; width:75%; max-height:85vh; overflow-y:auto; border-radius:6px; position:relative; box-shadow:0 5px 15px rgba(0,0,0,0.3);">
				<h2 id="tx-modal-title" style="margin-top:0; border-bottom:1px solid #ddd; padding-bottom:10px;"><?php esc_html_e( 'Transaction Payload Details', 'woo-prime-connector' ); ?></h2>
				
				<div style="margin-bottom:15px;">
					<h4><?php esc_html_e( 'Request Data (JSON):', 'woo-prime-connector' ); ?></h4>
					<pre id="tx-modal-request" style="background:#1e1e1e; color:#4ec9b0; padding:12px; border-radius:4px; max-height:220px; overflow-y:auto; font-size:12px; line-height:1.4;"></pre>
				</div>

				<div style="margin-bottom:15px;">
					<h4><?php esc_html_e( 'Response Data (JSON):', 'woo-prime-connector' ); ?></h4>
					<pre id="tx-modal-response" style="background:#1e1e1e; color:#ce9178; padding:12px; border-radius:4px; max-height:220px; overflow-y:auto; font-size:12px; line-height:1.4;"></pre>
				</div>

				<div id="tx-modal-error-wrap" style="display:none; margin-bottom:15px;">
					<h4 style="color:#d63638;"><?php esc_html_e( 'Error Message:', 'woo-prime-connector' ); ?></h4>
					<div id="tx-modal-error" style="background:#fcf0f1; border:1px solid #d63638; color:#d63638; padding:10px; border-radius:4px; font-weight:bold;"></div>
				</div>

				<button type="button" id="tx-modal-close-btn" class="button button-secondary" style="float:right; margin-top:10px;"><?php esc_html_e( 'Close', 'woo-prime-connector' ); ?></button>
			</div>
		</div>
		<?php
	}
}
