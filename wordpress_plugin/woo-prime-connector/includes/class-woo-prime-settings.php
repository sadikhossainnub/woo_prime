<?php
/**
 * Woo Prime Settings Page
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class Woo_Prime_Settings {

	public static function init() {
		add_action( 'admin_menu', array( __CLASS__, 'add_admin_menu' ) );
		add_action( 'admin_init', array( __CLASS__, 'register_settings' ) );
	}

	public static function add_admin_menu() {
		add_submenu_page(
			'woocommerce',
			__( 'Woo Prime Settings', 'woo-prime-connector' ),
			__( 'Woo Prime ERPNext', 'woo-prime-connector' ),
			'manage_options',
			'woo-prime-settings',
			array( __CLASS__, 'render_settings_page' )
		);
	}

	public static function register_settings() {
		register_setting( 'woo_prime_options_group', 'woo_prime_erpnext_url' );
		register_setting( 'woo_prime_options_group', 'woo_prime_enable_pricing_rules' );
		register_setting( 'woo_prime_options_group', 'woo_prime_enable_loyalty' );
	}

	public static function render_settings_page() {
		?>
		<div class="wrap">
			<h1><?php esc_html_e( 'Woo Prime — ERPNext Integration Settings', 'woo-prime-connector' ); ?></h1>
			<form method="post" action="options.php">
				<?php
				settings_fields( 'woo_prime_options_group' );
				do_settings_sections( 'woo_prime_options_group' );
				?>
				<table class="form-table">
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'ERPNext Site URL', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="url" name="woo_prime_erpnext_url" value="<?php echo esc_attr( get_option( 'woo_prime_erpnext_url', 'http://127.0.0.1:8000' ) ); ?>" class="regular-text" placeholder="https://erp.yourdomain.com" required />
							<p class="description"><?php esc_html_e( 'Enter your ERPNext instance URL without trailing slash (e.g. https://erp.dressup.com.bd).', 'woo-prime-connector' ); ?></p>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'Enable ERPNext Pricing Rules', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="checkbox" name="woo_prime_enable_pricing_rules" value="1" <?php checked( 1, get_option( 'woo_prime_enable_pricing_rules', 1 ) ); ?> />
							<label><?php esc_html_e( 'Evaluate ERPNext Pricing Rules in WooCommerce Cart in Real-Time', 'woo-prime-connector' ); ?></label>
						</td>
					</tr>
					<tr valign="top">
						<th scope="row"><?php esc_html_e( 'Enable ERPNext Loyalty Points', 'woo-prime-connector' ); ?></th>
						<td>
							<input type="checkbox" name="woo_prime_enable_loyalty" value="1" <?php checked( 1, get_option( 'woo_prime_enable_loyalty', 1 ) ); ?> />
							<label><?php esc_html_e( 'Show Customer Loyalty Point Balance & Allow Redemption at Checkout', 'woo-prime-connector' ); ?></label>
						</td>
					</tr>
				</table>
				<?php submit_button(); ?>
			</form>
		</div>
		<?php
	}
}
