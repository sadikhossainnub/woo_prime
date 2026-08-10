<?php
/**
 * Loyalty Points Handler
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Loyalty {

	public static function init() {
		if ( get_option( 'woo_prime_enable_loyalty', 1 ) ) {
			add_action( 'woocommerce_before_checkout_form', array( __CLASS__, 'display_loyalty_notice' ) );
			add_action( 'woocommerce_cart_calculate_fees', array( __CLASS__, 'apply_loyalty_discount' ) );
			add_action( 'wp_loaded', array( __CLASS__, 'handle_redemption_toggle' ) );
		}
	}

	public static function handle_redemption_toggle() {
		if ( isset( $_GET['woo_prime_redeem'] ) ) {
			if ( '1' === $_GET['woo_prime_redeem'] ) {
				WC()->session->set( 'woo_prime_redeem_loyalty', true );
			} else {
				WC()->session->set( 'woo_prime_redeem_loyalty', false );
			}
			wp_safe_redirect( wc_get_checkout_url() );
			exit;
		}
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
		$response     = wp_remote_get(
			add_query_arg(
				array( 'customer_email' => $current_user->user_email ),
				$erpnext_url . '/api/method/woo_prime.api.loyalty.get_customer_loyalty_points'
			),
			array( 'timeout' => 10 )
		);

		if ( is_wp_error( $response ) ) {
			return;
		}

		$body = wp_remote_retrieve_body( $response );
		$data = json_decode( $body, true );

		if ( isset( $data['message']['loyalty_points'] ) && $data['message']['loyalty_points'] > 0 ) {
			$points     = $data['message']['loyalty_points'];
			$value      = $data['message']['redeemable_amount'];
			$is_redeemed = WC()->session ? WC()->session->get( 'woo_prime_redeem_loyalty' ) : false;

			if ( ! $is_redeemed ) {
				$notice = sprintf(
					__( '🎁 You have <strong>%1$d Loyalty Points</strong> (Worth ৳%2$.2f). <a href="%3$s" class="button woo-prime-btn">Redeem Points Now</a>', 'woo-prime-connector' ),
					$points,
					$value,
					esc_url( add_query_arg( 'woo_prime_redeem', '1' ) )
				);
			} else {
				$notice = sprintf(
					__( '✅ Applied <strong>%1$d Loyalty Points</strong> (৳%2$.2f Discount Applied). <a href="%3$s">Remove Points</a>', 'woo-prime-connector' ),
					$points,
					$value,
					esc_url( add_query_arg( 'woo_prime_redeem', '0' ) )
				);
			}

			wc_print_notice( $notice, 'notice' );
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
		$response     = wp_remote_get(
			add_query_arg(
				array( 'customer_email' => $current_user->user_email ),
				$erpnext_url . '/api/method/woo_prime.api.loyalty.get_customer_loyalty_points'
			),
			array( 'timeout' => 10 )
		);

		if ( is_wp_error( $response ) ) {
			return;
		}

		$body = wp_remote_retrieve_body( $response );
		$data = json_decode( $body, true );

		$amount = isset( $data['message']['redeemable_amount'] ) ? $data['message']['redeemable_amount'] : 0;
		if ( $amount > 0 ) {
			$cart->add_fee( __( 'ERPNext Loyalty Points Redemption', 'woo-prime-connector' ), -$amount );
		}
	}
}
