jQuery(document).ready(function($) {
	// Test Connection
	$('#woo-prime-test-conn-btn').on('click', function(e) {
		e.preventDefault();
		var $btn = $(this);
		var $status = $('#woo-prime-action-status');

		$btn.prop('disabled', true).text('Testing Connection...');
		$status.text('');

		$.ajax({
			url: woo_prime_admin_params.ajax_url,
			type: 'POST',
			data: {
				action: 'woo_prime_test_connection',
				security: woo_prime_admin_params.nonce
			},
			success: function(response) {
				$btn.prop('disabled', false).text('Test Connection to ERPNext');
				if (response.success) {
					$status.css('color', 'green').text(response.data);
				} else {
					$status.css('color', 'red').text(response.data);
				}
			},
			error: function() {
				$btn.prop('disabled', false).text('Test Connection to ERPNext');
				$status.css('color', 'red').text('AJAX Request Failed.');
			}
		});
	});

	// Clear Cache
	$('#woo-prime-clear-cache-btn').on('click', function(e) {
		e.preventDefault();
		var $status = $('#woo-prime-action-status');
		$.post(woo_prime_admin_params.ajax_url, {
			action: 'woo_prime_clear_cache',
			security: woo_prime_admin_params.nonce
		}, function(res) {
			if (res.success) {
				$status.css('color', 'green').text(res.data);
			}
		});
	});

	// View Logs Modal
	$('#woo-prime-view-logs-btn').on('click', function(e) {
		e.preventDefault();
		$('#woo-prime-log-modal').show();
		$('#woo-prime-log-content').text('Loading logs...');

		$.post(woo_prime_admin_params.ajax_url, {
			action: 'woo_prime_get_logs',
			security: woo_prime_admin_params.nonce
		}, function(res) {
			if (res.success) {
				$('#woo-prime-log-content').text(res.data);
			}
		});
	});

	$('#woo-prime-close-log-modal').on('click', function() {
		$('#woo-prime-log-modal').hide();
	});

	$('#woo-prime-clear-logs-btn').on('click', function() {
		if (confirm('Are you sure you want to clear log file?')) {
			$.post(woo_prime_admin_params.ajax_url, {
				action: 'woo_prime_clear_logs',
				security: woo_prime_admin_params.nonce
			}, function(res) {
				if (res.success) {
					$('#woo-prime-log-content').text('Log file cleared.');
				}
			});
		}
	});
});
