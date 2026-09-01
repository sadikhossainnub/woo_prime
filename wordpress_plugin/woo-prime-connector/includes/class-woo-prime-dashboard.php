<?php
/**
 * Woo Prime Dashboard Widget Class
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Dashboard {

	public static function init() {
		add_action( 'wp_dashboard_setup', array( __CLASS__, 'add_dashboard_widget' ) );
	}

	public static function add_dashboard_widget() {
		wp_add_dashboard_widget(
			'woo_prime_dashboard_widget',
			__( 'Woo Prime — ERPNext Integration', 'woo-prime-connector' ),
			array( __CLASS__, 'render_widget' )
		);
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
					Woo_Prime_Cache::set( $cache_key, $stats, 600 ); // Cache 10 mins
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
				<a href="<?php echo esc_url( admin_url( 'admin.php?page=woo-prime-settings' ) ); ?>" class="button button-secondary"><?php esc_html_e( 'Woo Prime Settings', 'woo-prime-connector' ); ?></a>
			</p>
		</div>
		<?php
	}
}
