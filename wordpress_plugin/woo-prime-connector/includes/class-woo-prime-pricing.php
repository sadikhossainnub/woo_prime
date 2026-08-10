<?php
/**
 * Pricing Rules Calculation Handler
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Pricing {

	public static function init() {
		if ( get_option( 'woo_prime_enable_pricing_rules', 1 ) ) {
			add_action( 'woocommerce_cart_calculate_fees', array( __CLASS__, 'apply_pricing_rules' ) );
		}
	}

	public static function apply_pricing_rules( $cart ) {
		if ( is_admin() && ! defined( 'DOING_AJAX' ) ) {
			return;
		}

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			return;
		}

		$current_user   = wp_get_current_user();
		$customer_email = $current_user->exists() ? $current_user->user_email : '';

		$cart_items = array();
		foreach ( $cart->get_cart() as $cart_item_key => $values ) {
			$product = $values['data'];
			$cart_items[] = array(
				'sku'  => $product->get_sku(),
				'qty'  => $values['quantity'],
				'rate' => $product->get_price(),
			);
		}

		if ( empty( $cart_items ) ) {
			return;
		}

		$response = wp_remote_post(
			$erpnext_url . '/api/method/woo_prime.api.price.calculate_cart_price',
			array(
				'headers' => array( 'Content-Type' => 'application/json' ),
				'body'    => wp_json_encode( array(
					'cart_data'      => $cart_items,
					'customer_email' => $customer_email,
				) ),
				'timeout' => 15,
			)
		);

		if ( is_wp_error( $response ) ) {
			return;
		}

		$body = wp_remote_retrieve_body( $response );
		$data = json_decode( $body, true );

		if ( isset( $data['message']['total_cart_discount'] ) && $data['message']['total_cart_discount'] > 0 ) {
			$discount = $data['message']['total_cart_discount'];
			$cart->add_fee( __( 'ERPNext Pricing Rule Discount', 'woo-prime-connector' ), -$discount );
		}
	}
}
