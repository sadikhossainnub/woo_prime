<?php
/**
 * Plugin Name:       Woo Prime Connector
 * Plugin URI:        https://primetechbd.com
 * Description:       Integrates WooCommerce with ERPNext for real-time Pricing Rules calculation and Loyalty Points redemption.
 * Version:           1.0.0
 * Author:            Prime Tech BD
 * Author URI:        https://primetechbd.com
 * Text Domain:       woo-prime-connector
 * Domain Path:       /languages
 * Requires at least: 5.8
 * Requires PHP:      7.4
 * WC requires at least: 6.0
 * WC tests up to:    8.5
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit; // Exit if accessed directly.
}

define( 'WOO_PRIME_VERSION', '1.0.0' );
define( 'WOO_PRIME_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'WOO_PRIME_PLUGIN_URL', plugin_dir_url( __FILE__ ) );

// Include Core Classes
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-settings.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-pricing.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-loyalty.php';

/**
 * Initialize Main Plugin Class
 */
class Woo_Prime_Connector_Main {

	private static $instance = null;

	public static function get_instance() {
		if ( null === self::$instance ) {
			self::$instance = new self();
		}
		return self::$instance;
	}

	private function __construct() {
		add_action( 'plugins_loaded', array( $this, 'init_plugin' ) );
		add_action( 'wp_enqueue_scripts', array( $this, 'enqueue_assets' ) );
	}

	public function init_plugin() {
		// Check if WooCommerce is active
		if ( ! class_exists( 'WooCommerce' ) ) {
			add_action( 'admin_notices', array( $this, 'woocommerce_missing_notice' ) );
			return;
		}

		// Initialize Settings, Pricing Rules & Loyalty Modules
		Woo_Prime_Settings::init();
		Woo_Prime_Pricing::init();
		Woo_Prime_Loyalty::init();
	}

	public function enqueue_assets() {
		if ( is_cart() || is_checkout() ) {
			wp_enqueue_style(
				'woo-prime-style',
				WOO_PRIME_PLUGIN_URL . 'assets/css/woo-prime.css',
				array(),
				WOO_PRIME_VERSION
			);
		}
	}

	public function woocommerce_missing_notice() {
		echo '<div class="error"><p>' . esc_html__( 'Woo Prime Connector requires WooCommerce to be installed and active.', 'woo-prime-connector' ) . '</p></div>';
	}
}

Woo_Prime_Connector_Main::get_instance();
