<?php
/**
 * Loyalty Points Handler (v2.0 with AJAX support)
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Loyalty {

	public static function init() {
		if ( get_option( 'woo_prime_enable_loyalty', 1 ) ) {
			add_action( 'woocommerce_before_checkout_form', array( __CLASS__, 'display_loyalty_notice' ) );
			add_action( 'woocommerce_cart_calculate_fees', array( __CLASS__, 'apply_loyalty_discount' ) );
			add_action( 'wp_ajax_woo_prime_toggle_loyalty', array( __CLASS__, 'ajax_toggle_loyalty' ) );
			add_action( 'wp_ajax_nopriv_woo_prime_toggle_loyalty', array( __CLASS__, 'ajax_toggle_loyalty' ) );
		}
	}

	public static function ajax_toggle_loyalty() {
		check_ajax_referer( 'woo_prime_checkout_nonce', 'security' );

		$redeem = isset( $_POST['redeem'] ) && '1' === $_POST['redeem'];
		if ( WC()->session ) {
			WC()->session->set( 'woo_prime_redeem_loyalty', $redeem );
		}

		wp_send_json_success( array(
			'redeemed' => $redeem,
			'message'  => $redeem ? __( 'Loyalty points discount applied!', 'woo-prime-connector' ) : __( 'Loyalty points removed.', 'woo-prime-connector' ),
		) );
	}

	public static function display_loyalty_notice() {
		if ( ! is_user_logged_in() ) {
			return;
		}

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			return;
		}

		$current_user = wp_get_current_user();
		$cache_key    = 'loyalty_' . md5( $current_user->user_email );
		$data         = Woo_Prime_Cache::get( $cache_key );

		if ( false === $data ) {
			$api_key    = get_option( 'woo_prime_api_key' );
			$api_secret = get_option( 'woo_prime_api_secret' );
			$headers    = array( 'Content-Type' => 'application/json' );

			if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
				$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
			}

			$response = wp_remote_get(
				add_query_arg(
					array( 'customer_email' => $current_user->user_email ),
					$erpnext_url . '/api/method/woo_prime.api.loyalty.get_customer_loyalty_points'
				),
				array( 'headers' => $headers, 'timeout' => 8 )
			);

			if ( is_wp_error( $response ) ) {
				Woo_Prime_Logger::log( 'error', 'Loyalty points API failed', array( 'error' => $response->get_error_message() ) );
				return;
			}

			$body = wp_remote_retrieve_body( $response );
			$res  = json_decode( $body, true );
			$data = isset( $res['message'] ) ? $res['message'] : null;

			if ( $data ) {
				Woo_Prime_Cache::set( $cache_key, $data, 120 ); // Cache 2 mins
			}
		}

		if ( isset( $data['loyalty_points'] ) && $data['loyalty_points'] > 0 ) {
			$points      = $data['loyalty_points'];
			$value       = $data['redeemable_amount'];
			$is_redeemed = WC()->session ? WC()->session->get( 'woo_prime_redeem_loyalty' ) : false;

			?>
			<div class="woocommerce-info woo-prime-loyalty-box" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap;">
				<div>
					<?php if ( ! $is_redeemed ) : ?>
						🎁 <?php printf( __( 'You have <strong>%1$d Loyalty Points</strong> (Worth ৳%2$.2f in ERPNext).', 'woo-prime-connector' ), $points, $value ); ?>
					<?php else : ?>
						✅ <?php printf( __( 'Applied <strong>%1$d Loyalty Points</strong> (৳%2$.2f Discount Applied).', 'woo-prime-connector' ), $points, $value ); ?>
					<?php endif; ?>
				</div>
				<div>
					<?php if ( ! $is_redeemed ) : ?>
						<button type="button" class="button woo-prime-btn woo-prime-loyalty-toggle" data-redeem="1"><?php esc_html_e( 'Redeem Points Now', 'woo-prime-connector' ); ?></button>
					<?php else : ?>
						<button type="button" class="button button-secondary woo-prime-loyalty-toggle" data-redeem="0"><?php esc_html_e( 'Remove Points', 'woo-prime-connector' ); ?></button>
					<?php endif; ?>
				</div>
			</div>
			<?php
		}
	}

	public static function apply_loyalty_discount( $cart ) {
		if ( ! is_user_logged_in() || ! WC()->session ) {
			return;
		}

		if ( ! WC()->session->get( 'woo_prime_redeem_loyalty' ) ) {
			return;
		}

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			return;
		}

		$current_user = wp_get_current_user();
		$cache_key    = 'loyalty_' . md5( $current_user->user_email );
		$data         = Woo_Prime_Cache::get( $cache_key );

		if ( false === $data ) {
			$api_key    = get_option( 'woo_prime_api_key' );
			$api_secret = get_option( 'woo_prime_api_secret' );
			$headers    = array( 'Content-Type' => 'application/json' );

			if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
				$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
			}

			$response = wp_remote_get(
				add_query_arg(
					array( 'customer_email' => $current_user->user_email ),
					$erpnext_url . '/api/method/woo_prime.api.loyalty.get_customer_loyalty_points'
				),
				array( 'headers' => $headers, 'timeout' => 8 )
			);

			if ( is_wp_error( $response ) ) {
				return;
			}

			$body = wp_remote_retrieve_body( $response );
			$res  = json_decode( $body, true );
			$data = isset( $res['message'] ) ? $res['message'] : null;
		}

		$amount = isset( $data['redeemable_amount'] ) ? floatval( $data['redeemable_amount'] ) : 0;
		if ( $amount > 0 ) {
			$cart->add_fee( __( 'ERPNext Loyalty Points Redemption', 'woo-prime-connector' ), -$amount );
		}
	}
}
