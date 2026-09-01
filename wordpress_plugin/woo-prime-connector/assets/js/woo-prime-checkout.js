jQuery(document).ready(function($) {
	$(document).on('click', '.woo-prime-loyalty-toggle', function(e) {
		e.preventDefault();
		var $btn = $(this);
		var redeem = $btn.data('redeem');

		$btn.prop('disabled', true).text('Updating...');

		$.ajax({
			url: woo_prime_checkout_params.ajax_url,
			type: 'POST',
			data: {
				action: 'woo_prime_toggle_loyalty',
				security: woo_prime_checkout_params.nonce,
				redeem: redeem
			},
			success: function(res) {
				if (res.success) {
					// Trigger WooCommerce checkout update
					$(document.body).trigger('update_checkout');
				} else {
					alert(res.data || 'Failed to update loyalty points.');
					$btn.prop('disabled', false);
				}
			},
			error: function() {
				alert('Connection error.');
				$btn.prop('disabled', false);
			}
		});
	});
});
