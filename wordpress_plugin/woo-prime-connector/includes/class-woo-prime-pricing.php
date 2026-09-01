<?php
/**
 * Pricing Rules Calculation Handler (v2.0 with per-item pricing breakdown and caching)
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Pricing {

	public static function init() {
		if ( get_option( 'woo_prime_enable_pricing_rules', 1 ) ) {
			add_action( 'woocommerce_before_calculate_totals', array( __CLASS__, 'apply_per_item_pricing' ), 20, 1 );
			add_filter( 'woocommerce_cart_item_price', array( __CLASS__, 'display_item_price_strikethrough' ), 10, 3 );
		}
	}

	public static function apply_per_item_pricing( $cart ) {
		if ( is_admin() && ! defined( 'DOING_AJAX' ) ) {
			return;
		}

		static $already_run = false;
		if ( $already_run ) {
			return;
		}
		$already_run = true;

		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			return;
		}

		$current_user   = wp_get_current_user();
		$customer_email = $current_user->exists() ? $current_user->user_email : '';

		$cart_items = array();
		foreach ( $cart->get_cart() as $cart_item_key => $values ) {
			$product      = $values['data'];
			$cart_items[] = array(
				'sku'       => $product->get_sku(),
				'item_code' => $product->get_sku(),
				'qty'       => $values['quantity'],
				'rate'      => $product->get_regular_price() ? $product->get_regular_price() : $product->get_price(),
				'key'       => $cart_item_key,
			);
		}

		if ( empty( $cart_items ) ) {
			return;
		}

		$cart_hash = md5( wp_json_encode( $cart_items ) . $customer_email );
		$cache_key = 'pricing_' . $cart_hash;
		$data      = Woo_Prime_Cache::get( $cache_key );

		if ( false === $data ) {
			$api_key    = get_option( 'woo_prime_api_key' );
			$api_secret = get_option( 'woo_prime_api_secret' );
			$headers    = array( 'Content-Type' => 'application/json' );

			if ( ! empty( $api_key ) && ! empty( $api_secret ) ) {
				$headers['Authorization'] = 'token ' . $api_key . ':' . $api_secret;
			}

			$response = wp_remote_post(
				$erpnext_url . '/api/method/woo_prime.api.price.calculate_cart_price',
				array(
					'headers' => $headers,
					'body'    => wp_json_encode( array(
						'cart_data'      => $cart_items,
						'customer_email' => $customer_email,
					) ),
					'timeout' => 12,
				)
			);

			if ( is_wp_error( $response ) ) {
				Woo_Prime_Logger::log( 'error', 'Pricing calculation API call failed', array( 'error' => $response->get_error_message() ) );
				return;
			}

			$body = wp_remote_retrieve_body( $response );
			$res  = json_decode( $body, true );
			$data = isset( $res['message'] ) ? $res['message'] : null;

			if ( $data ) {
				Woo_Prime_Cache::set( $cache_key, $data );
			}
		}

		if ( empty( $data['items'] ) ) {
			return;
		}

		// Map evaluated final rates back to cart items by SKU
		$evaluated_map = array();
		foreach ( $data['items'] as $eval_item ) {
			if ( ! empty( $eval_item['sku'] ) ) {
				$evaluated_map[ $eval_item['sku'] ] = $eval_item;
			}
		}

		foreach ( $cart->get_cart() as $cart_item_key => $values ) {
			$product = $values['data'];
			$sku     = $product->get_sku();

			if ( isset( $evaluated_map[ $sku ] ) ) {
				$eval_data  = $evaluated_map[ $sku ];
				$final_rate = floatval( $eval_data['final_rate'] );
				$orig_rate  = floatval( $eval_data['original_rate'] );

				if ( $final_rate < $orig_rate && $final_rate >= 0 ) {
					$product->set_price( $final_rate );
					$values['data']->woo_prime_orig_price = $orig_rate;
				}
			}
		}
	}

	public static function display_item_price_strikethrough( $price, $cart_item, $cart_item_key ) {
		if ( isset( $cart_item['data']->woo_prime_orig_price ) && $cart_item['data']->woo_prime_orig_price > $cart_item['data']->get_price() ) {
			$orig_price = wc_price( $cart_item['data']->woo_prime_orig_price );
			$curr_price = wc_price( $cart_item['data']->get_price() );
			return sprintf( '<del>%s</del> <ins>%s</ins> <span class="woo-prime-badge">%s</span>', $orig_price, $curr_price, __( 'ERPNext Pricing Rule', 'woo-prime-connector' ) );
		}
		return $price;
	}
}
