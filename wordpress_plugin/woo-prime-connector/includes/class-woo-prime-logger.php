<?php
/**
 * Woo Prime Logger Class
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Logger {

	public static function log( $level, $message, $context = array() ) {
		if ( ! get_option( 'woo_prime_enable_logging', 1 ) ) {
			return;
		}

		$log_file = WP_CONTENT_DIR . '/woo-prime-connector-debug.log';

		// Rotate log file if > 500KB
		if ( file_exists( $log_file ) && filesize( $log_file ) > 512000 ) {
			@rename( $log_file, $log_file . '.old' );
		}

		$time        = date( 'Y-m-d H:i:s' );
		$context_str = ! empty( $context ) ? ' ' . wp_json_encode( $context ) : '';
		$log_entry   = sprintf( "[%s] [%s]: %s%s\n", $time, strtoupper( $level ), $message, $context_str );

		@error_log( $log_entry, 3, $log_file );
	}

	public static function get_logs( $lines = 100 ) {
		$log_file = WP_CONTENT_DIR . '/woo-prime-connector-debug.log';
		if ( ! file_exists( $log_file ) ) {
			return __( 'No log file found.', 'woo-prime-connector' );
		}

		$file = file( $log_file );
		if ( empty( $file ) ) {
			return __( 'Log file is empty.', 'woo-prime-connector' );
		}

		$last_lines = array_slice( $file, -$lines );
		return implode( '', $last_lines );
	}

	public static function clear_logs() {
		$log_file = WP_CONTENT_DIR . '/woo-prime-connector-debug.log';
		if ( file_exists( $log_file ) ) {
			@unlink( $log_file );
		}
	}
}
