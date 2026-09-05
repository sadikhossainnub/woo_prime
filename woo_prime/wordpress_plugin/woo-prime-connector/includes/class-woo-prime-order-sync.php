<?php
/**
 * Woo Prime Order Sync — Direct order push to ERPNext
 *
 * Sends WooCommerce order data to ERPNext via REST API immediately
 * when a new order is placed on the website. This works as a reliable
 * companion to WooCommerce webhooks (which can sometimes miss or delay).
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Order_Sync {

	public static function init() {
		// Fire when WooCommerce processes payment / creates order
		add_action( 'woocommerce_checkout_order_processed', array( __CLASS__, 'on_order_created' ), 10, 3 );

		// Also fire on order status changes (processing, completed)
		add_action( 'woocommerce_order_status_processing', array( __CLASS__, 'on_order_status_change' ), 10, 2 );
		add_action( 'woocommerce_order_status_completed', array( __CLASS__, 'on_order_status_change' ), 10, 2 );

		// Fire on payment complete (covers gateways that redirect back)
		add_action( 'woocommerce_payment_complete', array( __CLASS__, 'on_payment_complete' ), 10, 1 );

		// REST API endpoint for manual sync from WP admin
		add_action( 'wp_ajax_woo_prime_sync_order', array( __CLASS__, 'ajax_sync_order' ) );
	}

	/**
	 * Hook: woocommerce_checkout_order_processed
	 *
	 * Fires immediately after WooCommerce creates the order during checkout.
	 */
	public static function on_order_created( $order_id, $posted_data, $order ) {
		if ( ! $order_id ) {
			return;
		}

		// Don't send if order is still "pending" payment (wait for payment_complete)
		if ( $order && method_exists( $order, 'get_status' ) ) {
			$status = $order->get_status();
			// For COD and direct bank transfer, status is usually "on-hold" or "processing"
			$immediate_statuses = array( 'processing', 'completed', 'on-hold' );
			if ( in_array( $status, $immediate_statuses, true ) ) {
				self::push_order_to_erpnext( $order_id );
			}
			// For "pending" (e.g. Bkash/SSLCommerz redirect), we wait for payment_complete hook
		}
	}

	/**
	 * Hook: woocommerce_payment_complete
	 *
	 * Fires when a payment gateway confirms payment.
	 * Covers redirect-based gateways (Bkash, SSLCommerz, PayPal, etc.)
	 */
	public static function on_payment_complete( $order_id ) {
		self::push_order_to_erpnext( $order_id );
	}

	/**
	 * Hook: woocommerce_order_status_{processing|completed}
	 *
	 * Fires on order status transitions — as a safety net.
	 */
	public static function on_order_status_change( $order_id, $order = null ) {
		self::push_order_to_erpnext( $order_id );
	}

	/**
	 * Push order data to ERPNext via REST API
	 *
	 * Uses a transient lock to prevent duplicate sends for the same order.
	 */
	public static function push_order_to_erpnext( $order_id ) {
		$erpnext_url = rtrim( get_option( 'woo_prime_erpnext_url' ), '/' );
		if ( empty( $erpnext_url ) ) {
			return;
		}

		$api_key    = get_option( 'woo_prime_api_key' );
		$api_secret = get_option( 'woo_prime_api_secret' );
		if ( empty( $api_key ) || empty( $api_secret ) ) {
			return;
		}

		// Prevent duplicate sends with a transient lock (valid for 5 minutes)
		$lock_key = 'woo_prime_order_sync_' . $order_id;
		if ( get_transient( $lock_key ) ) {
			Woo_Prime_Logger::log( 'Order #' . $order_id . ' already sent to ERPNext (duplicate prevented).' );
			return;
		}
		set_transient( $lock_key, true, 5 * MINUTE_IN_SECONDS );

		$order = wc_get_order( $order_id );
		if ( ! $order ) {
			Woo_Prime_Logger::log( 'Order #' . $order_id . ' not found.' );
			return;
		}

		// Build WooCommerce REST API compatible order payload
		$order_data = self::build_order_payload( $order );

		// Send to ERPNext
		$endpoint = $erpnext_url . '/api/method/woo_prime.api.webhook.handle_order';

		$headers = array(
			'Content-Type'      => 'application/json',
			'X-WC-Webhook-Topic' => 'order.created',
		);

		// Add webhook HMAC signature if secret is configured in WooCommerce
		$body_json = wp_json_encode( $order_data );

		$response = wp_remote_post( $endpoint, array(
			'headers'   => $headers,
			'body'      => $body_json,
			'timeout'   => 30,
			'sslverify' => false,
		) );

		if ( is_wp_error( $response ) ) {
			Woo_Prime_Logger::log( 'ERPNext order sync failed for #' . $order_id . ': ' . $response->get_error_message() );
			// Clear lock so it can be retried
			delete_transient( $lock_key );
			return;
		}

		$status_code = wp_remote_retrieve_response_code( $response );
		$resp_body   = wp_remote_retrieve_body( $response );

		if ( $status_code >= 200 && $status_code < 300 ) {
			Woo_Prime_Logger::log( 'Order #' . $order_id . ' successfully sent to ERPNext. Response: ' . $resp_body );

			// Store meta on the order to track sync status
			$order->update_meta_data( '_woo_prime_synced', 'yes' );
			$order->update_meta_data( '_woo_prime_sync_time', current_time( 'mysql' ) );
			$order->save();
		} else {
			Woo_Prime_Logger::log( 'ERPNext order sync failed for #' . $order_id . '. HTTP ' . $status_code . ': ' . $resp_body );
			// Clear lock so it can be retried
			delete_transient( $lock_key );
		}
	}

	/**
	 * Build WooCommerce REST API-compatible order payload from WC_Order
	 *
	 * This creates the same structure that WooCommerce webhooks/REST API returns,
	 * so the ERPNext webhook handler can process it identically.
	 */
	public static function build_order_payload( $order ) {
		$data = array(
			'id'               => $order->get_id(),
			'status'           => $order->get_status(),
			'currency'         => $order->get_currency(),
			'date_created'     => $order->get_date_created() ? $order->get_date_created()->format( 'Y-m-d\TH:i:s' ) : '',
			'total'            => $order->get_total(),
			'discount_total'   => $order->get_discount_total(),
			'shipping_total'   => $order->get_shipping_total(),
			'customer_id'      => $order->get_customer_id(),
			'customer_note'    => $order->get_customer_note(),
			'payment_method'   => $order->get_payment_method(),
			'payment_method_title' => $order->get_payment_method_title(),
		);

		// Billing
		$data['billing'] = array(
			'first_name' => $order->get_billing_first_name(),
			'last_name'  => $order->get_billing_last_name(),
			'company'    => $order->get_billing_company(),
			'address_1'  => $order->get_billing_address_1(),
			'address_2'  => $order->get_billing_address_2(),
			'city'       => $order->get_billing_city(),
			'state'      => $order->get_billing_state(),
			'postcode'   => $order->get_billing_postcode(),
			'country'    => $order->get_billing_country(),
			'email'      => $order->get_billing_email(),
			'phone'      => $order->get_billing_phone(),
		);

		// Shipping
		$data['shipping'] = array(
			'first_name' => $order->get_shipping_first_name(),
			'last_name'  => $order->get_shipping_last_name(),
			'company'    => $order->get_shipping_company(),
			'address_1'  => $order->get_shipping_address_1(),
			'address_2'  => $order->get_shipping_address_2(),
			'city'       => $order->get_shipping_city(),
			'state'      => $order->get_shipping_state(),
			'postcode'   => $order->get_shipping_postcode(),
			'country'    => $order->get_shipping_country(),
		);

		// Line items
		$data['line_items'] = array();
		foreach ( $order->get_items() as $item ) {
			$product      = $item->get_product();
			$variation_id = $item->get_variation_id();

			$line = array(
				'name'         => $item->get_name(),
				'product_id'   => $item->get_product_id(),
				'variation_id' => $variation_id ? $variation_id : 0,
				'quantity'     => $item->get_quantity(),
				'sku'          => $product ? $product->get_sku() : '',
				'price'        => ( $item->get_total() / max( $item->get_quantity(), 1 ) ),
				'total'        => $item->get_total(),
				'subtotal'     => $item->get_subtotal(),
			);

			$data['line_items'][] = $line;
		}

		// Shipping lines
		$data['shipping_lines'] = array();
		foreach ( $order->get_shipping_methods() as $shipping ) {
			$data['shipping_lines'][] = array(
				'method_title' => $shipping->get_method_title(),
				'method_id'    => $shipping->get_method_id(),
				'total'        => $shipping->get_total(),
			);
		}

		// Coupon lines
		$data['coupon_lines'] = array();
		foreach ( $order->get_coupons() as $coupon ) {
			$data['coupon_lines'][] = array(
				'code'     => $coupon->get_code(),
				'discount' => $coupon->get_discount(),
			);
		}

		return $data;
	}

	/**
	 * AJAX handler: Manual order sync from WP admin
	 */
	public static function ajax_sync_order() {
		check_ajax_referer( 'woo_prime_admin_nonce', 'security' );

		$order_id = isset( $_POST['order_id'] ) ? absint( $_POST['order_id'] ) : 0;
		if ( ! $order_id ) {
			wp_send_json_error( 'Invalid order ID.' );
		}

		// Clear any existing lock to force re-sync
		delete_transient( 'woo_prime_order_sync_' . $order_id );

		self::push_order_to_erpnext( $order_id );

		$order    = wc_get_order( $order_id );
		$synced   = $order ? $order->get_meta( '_woo_prime_synced' ) : '';
		if ( 'yes' === $synced ) {
			wp_send_json_success( 'Order #' . $order_id . ' synced to ERPNext successfully!' );
		} else {
			wp_send_json_error( 'Order sync may have failed. Check ERPNext Error Log for details.' );
		}
	}
}
