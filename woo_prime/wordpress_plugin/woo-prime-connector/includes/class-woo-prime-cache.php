<?php
/**
 * Woo Prime Cache Class using WordPress Transients API
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Cache {

	public static function get( $key ) {
		return get_transient( 'woo_prime_' . $key );
	}

	public static function set( $key, $data, $expiration = null ) {
		if ( null === $expiration ) {
			$expiration = (int) get_option( 'woo_prime_cache_ttl', 5 ) * 60;
		}
		set_transient( 'woo_prime_' . $key, $data, $expiration );
	}

	public static function delete( $key ) {
		delete_transient( 'woo_prime_' . $key );
	}

	public static function clear_all() {
		global $wpdb;
		$wpdb->query( "DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient_woo_prime_%' OR option_name LIKE '_transient_timeout_woo_prime_%'" );
	}
}
