<?php
/**
 * Plugin Name:       Woo Prime Connector
 * Plugin URI:        https://primetechbd.com
 * Description:       Integrates WooCommerce with ERPNext for real-time Pricing Rules evaluation, Loyalty Points redemption, and sync dashboard.
 * Version:           2.0.0
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

define( 'WOO_PRIME_VERSION', '2.0.0' );
define( 'WOO_PRIME_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
define( 'WOO_PRIME_PLUGIN_URL', plugin_dir_url( __FILE__ ) );

// Include Core Classes
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-logger.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-cache.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-settings.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-pricing.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-loyalty.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-dashboard.php';
require_once WOO_PRIME_PLUGIN_DIR . 'includes/class-woo-prime-order-sync.php';

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
		add_action( 'wp_enqueue_scripts', array( $this, 'enqueue_frontend_assets' ) );
		add_action( 'admin_enqueue_scripts', array( $this, 'enqueue_admin_assets' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( __FILE__ ), array( $this, 'add_plugin_action_links' ) );
	}

	/**
	 * Add Settings action link on WP Plugins page
	 */
	public function add_plugin_action_links( $links ) {
		$settings_url  = admin_url( 'admin.php?page=woo-prime-settings' );
		$settings_link = '<a href="' . esc_url( $settings_url ) . '">' . esc_html__( 'Settings', 'woo-prime-connector' ) . '</a>';
		array_unshift( $links, $settings_link );
		return $links;
	}

	public function init_plugin() {
		// Always initialize Settings Module so settings page is accessible
		Woo_Prime_Settings::init();

		// Check if WooCommerce is active
		if ( ! class_exists( 'WooCommerce' ) ) {
			add_action( 'admin_notices', array( $this, 'woocommerce_missing_notice' ) );
			return;
		}

		// Initialize Pricing Rules, Loyalty & Dashboard Modules
		Woo_Prime_Pricing::init();
		Woo_Prime_Loyalty::init();
		Woo_Prime_Dashboard::init();
		Woo_Prime_Order_Sync::init();
	}

	public function enqueue_frontend_assets() {
		if ( is_cart() || is_checkout() ) {
			wp_enqueue_style(
				'woo-prime-style',
				WOO_PRIME_PLUGIN_URL . 'assets/css/woo-prime.css',
				array(),
				WOO_PRIME_VERSION
			);

			wp_enqueue_script(
				'woo-prime-checkout',
				WOO_PRIME_PLUGIN_URL . 'assets/js/woo-prime-checkout.js',
				array( 'jquery' ),
				WOO_PRIME_VERSION,
				true
			);

			wp_localize_script( 'woo-prime-checkout', 'woo_prime_checkout_params', array(
				'ajax_url' => admin_url( 'admin-ajax.php' ),
				'nonce'    => wp_create_nonce( 'woo_prime_checkout_nonce' ),
			) );
		}
	}

	public function enqueue_admin_assets( $hook ) {
		if ( false !== strpos( $hook, 'woo-prime-settings' ) ) {
			wp_enqueue_script(
				'woo-prime-admin',
				WOO_PRIME_PLUGIN_URL . 'assets/js/woo-prime-admin.js',
				array( 'jquery' ),
				WOO_PRIME_VERSION,
				true
			);

			wp_localize_script( 'woo-prime-admin', 'woo_prime_admin_params', array(
				'ajax_url' => admin_url( 'admin-ajax.php' ),
				'nonce'    => wp_create_nonce( 'woo_prime_admin_nonce' ),
			) );
		}
	}

	public function woocommerce_missing_notice() {
		echo '<div class="error"><p>' . esc_html__( 'Woo Prime Connector requires WooCommerce to be installed and active.', 'woo-prime-connector' ) . '</p></div>';
	}
}

Woo_Prime_Connector_Main::get_instance();
