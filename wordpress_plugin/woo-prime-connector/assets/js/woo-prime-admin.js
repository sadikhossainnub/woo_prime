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

	// Selective ERPNext Item Importer Logic
	function updateSelectedCount() {
		var count = $('.erpnext-item-cb:checked').length;
		$('#selected-items-count').text('Selected: ' + count + ' items');
		if (count > 0) {
			$('#woo-prime-import-selected-btn').prop('disabled', false).text('📥 Import Selected Items (' + count + ')');
		} else {
			$('#woo-prime-import-selected-btn').prop('disabled', true).text('📥 Import Selected Items (0)');
		}
	}

	function loadERPNextItems(searchQuery) {
		var $tbody = $('#erpnext-items-tbody');
		var $grid = $('#erpnext-items-grid-wrap');
		
		$grid.html('<div style="grid-column:1/-1; text-align:center; padding:30px; color:#2271b1; font-size:14px;">⏳ Loading live items from ERPNext...</div>');
		$tbody.html('<tr><td colspan="7" style="text-align:center; padding:30px; color:#2271b1; font-size:14px;">⏳ Loading live items from ERPNext...</td></tr>');
		$('#item-select-all-cb').prop('checked', false);
		updateSelectedCount();

		$.ajax({
			url: woo_prime_admin_params.ajax_url,
			type: 'POST',
			data: {
				action: 'woo_prime_list_erpnext_items',
				security: woo_prime_admin_params.nonce,
				search: searchQuery || ''
			},
			success: function(res) {
				if (res.success && res.data) {
					var items = res.data;
					if (!items.length) {
						var emptyMsg = '<div style="grid-column:1/-1; text-align:center; padding:30px; color:#646970;">No items found matching criteria in ERPNext.</div>';
						$grid.html(emptyMsg);
						$tbody.html('<tr><td colspan="7" style="text-align:center; padding:30px; color:#646970;">No items found matching criteria in ERPNext.</td></tr>');
						return;
					}

					var gridHtml = '';
					var rowsHtml = '';

					$.each(items, function(i, item) {
						var imgUrl = item.image_url || '';
						var imgHtmlTable = imgUrl 
							? '<img src="' + imgUrl + '" style="width:36px; height:36px; object-fit:cover; border-radius:4px; border:1px solid #ccc;" />'
							: '<div style="width:36px; height:36px; background:#eee; border-radius:4px; display:inline-flex; align-items:center; justify-content:center; color:#999; font-size:9px;">No Img</div>';

						var wcBadge = item.is_in_wc
							? '<span style="background:#e7f8ed; color:#00a32a; padding:2px 6px; border-radius:3px; font-weight:600; font-size:10px;">🟢 In WC</span>'
							: '<span style="background:#e8f0fe; color:#1a73e8; padding:2px 6px; border-radius:3px; font-weight:600; font-size:10px;">✨ New Item</span>';

						// Grid Card HTML
						var cardImg = imgUrl
							? '<img src="' + imgUrl + '" style="width:100%; height:110px; object-fit:cover; border-bottom:1px solid #eee;" />'
							: '<div style="width:100%; height:110px; background:#f0f2f5; display:flex; align-items:center; justify-content:center; color:#8c8f94; font-size:11px; font-weight:bold;">No Image</div>';

						gridHtml += '<div class="erpnext-item-card" style="background:#fff; border:1px solid #c3c4c7; border-radius:6px; overflow:hidden; position:relative; display:flex; flex-direction:column; box-shadow:0 1px 3px rgba(0,0,0,0.04); transition:all 0.2s ease;">' +
							'<div style="position:absolute; top:6px; left:6px; z-index:2; background:rgba(255,255,255,0.92); border-radius:4px; padding:2px 6px; box-shadow:0 1px 3px rgba(0,0,0,0.15);">' +
								'<input type="checkbox" class="erpnext-item-cb" value="' + item.sku + '" style="margin:0; cursor:pointer;" />' +
							'</div>' +
							'<div style="position:absolute; top:6px; right:6px; z-index:2;">' + wcBadge + '</div>' +
							cardImg +
							'<div style="padding:10px; flex:1; display:flex; flex-direction:column; justify-content:space-between;">' +
								'<div>' +
									'<div style="font-weight:bold; font-size:12px; color:#1d2327; line-height:1.3; height:32px; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;">' + (item.item_name || item.sku) + '</div>' +
									'<div style="font-size:10px; color:#646970; margin-top:2px;">SKU: <code>' + item.sku + '</code></div>' +
								'</div>' +
								'<div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; padding-top:6px; border-top:1px solid #f0f0f0;">' +
									'<span style="font-weight:bold; color:#00a32a; font-size:12px;">$' + (item.price ? item.price : '0.00') + '</span>' +
									'<span style="font-size:10px; color:#646970;">Stock: <strong>' + (item.stock_quantity || 0) + '</strong></span>' +
								'</div>' +
							'</div>' +
						'</div>';

						// Table Row HTML
						rowsHtml += '<tr>' +
							'<td style="text-align:center;"><input type="checkbox" class="erpnext-item-cb" value="' + item.sku + '" /></td>' +
							'<td>' + imgHtmlTable + '</td>' +
							'<td><code>' + item.sku + '</code></td>' +
							'<td><strong>' + (item.item_name || item.sku) + '</strong></td>' +
							'<td>$' + (item.price ? item.price : '0.00') + '</td>' +
							'<td>' + (item.stock_quantity || 0) + '</td>' +
							'<td style="text-align:center;">' + wcBadge + '</td>' +
						'</tr>';
					});

					$grid.html(gridHtml);
					$tbody.html(rowsHtml);
				} else {
					var errText = res.data || 'Failed to fetch items from ERPNext.';
					$grid.html('<div style="grid-column:1/-1; text-align:center; padding:30px; color:#d63638;">' + errText + '</div>');
					$tbody.html('<tr><td colspan="7" style="text-align:center; padding:30px; color:#d63638;">' + errText + '</td></tr>');
				}
			},
			error: function() {
				var errText = 'AJAX Request Failed while listing items.';
				$grid.html('<div style="grid-column:1/-1; text-align:center; padding:30px; color:#d63638;">' + errText + '</div>');
				$tbody.html('<tr><td colspan="7" style="text-align:center; padding:30px; color:#d63638;">' + errText + '</td></tr>');
			}
		});
	}

	// View Mode Switcher
	$('#view-mode-grid-btn').on('click', function() {
		$(this).addClass('button-primary active-view-btn').removeClass('button-secondary');
		$('#view-mode-list-btn').addClass('button-secondary').removeClass('button-primary active-view-btn');
		$('#erpnext-items-grid-wrap').show();
		$('#erpnext-items-list-wrap').hide();
	});

	$('#view-mode-list-btn').on('click', function() {
		$(this).addClass('button-primary active-view-btn').removeClass('button-secondary');
		$('#view-mode-grid-btn').addClass('button-secondary').removeClass('button-primary active-view-btn');
		$('#erpnext-items-list-wrap').show();
		$('#erpnext-items-grid-wrap').hide();
	});

	$('#woo-prime-open-item-modal-btn').on('click', function(e) {
		e.preventDefault();
		$('#woo-prime-item-select-modal').show();
		$('#item-search-input').val('');
		loadERPNextItems('');
	});

	$('#item-modal-close-btn, #item-modal-close-top').on('click', function() {
		$('#woo-prime-item-select-modal').hide();
	});

	$('#item-search-btn').on('click', function() {
		var q = $('#item-search-input').val();
		loadERPNextItems(q);
	});

	$('#item-search-input').on('keyup', function(e) {
		if (e.keyCode === 13) {
			loadERPNextItems($(this).val());
		}
	});

	$('#item-reload-btn').on('click', function() {
		$('#item-search-input').val('');
		loadERPNextItems('');
	});

	// Select All Checkbox
	$('#item-select-all-cb').on('change', function() {
		var checked = $(this).is(':checked');
		$('.erpnext-item-cb').prop('checked', checked);
		updateSelectedCount();
	});

	// Item checkbox change
	$(document).on('change', '.erpnext-item-cb', function() {
		updateSelectedCount();
	});

	// Import Selected Items Button Click
	$('#woo-prime-import-selected-btn').on('click', function(e) {
		e.preventDefault();
		var selected = [];
		$('.erpnext-item-cb:checked').each(function() {
			selected.push($(this).val());
		});

		if (!selected.length) {
			alert('Please select at least one item to import.');
			return;
		}

		var $btn = $(this);
		$btn.prop('disabled', true).text('⏳ Importing ' + selected.length + ' items...');

		$.ajax({
			url: woo_prime_admin_params.ajax_url,
			type: 'POST',
			data: {
				action: 'woo_prime_import_selected_items',
				security: woo_prime_admin_params.nonce,
				selected_skus: selected
			},
			success: function(res) {
				$btn.prop('disabled', false).text('📥 Import Selected Items (' + selected.length + ')');
				if (res.success) {
					alert(res.data);
					loadERPNextItems($('#item-search-input').val());
				} else {
					alert('Import Error: ' + res.data);
				}
			},
			error: function() {
				$btn.prop('disabled', false).text('📥 Import Selected Items');
				alert('AJAX Request Failed during import.');
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

	// Sync Dashboard Logic
	function loadDashboardData() {
		if (!$('.woo-prime-dashboard-page').length) {
			return;
		}

		var $btn = $('#woo-prime-refresh-dash-btn');
		var $status = $('#dash-status-msg');
		var $tbody = $('#dash-transactions-tbody');

		$btn.prop('disabled', true).text('⏳ Loading...');
		$status.css('color', '#2271b1').text('Fetching live transactions...');

		$.ajax({
			url: woo_prime_admin_params.ajax_url,
			type: 'POST',
			data: {
				action: 'woo_prime_fetch_transactions',
				security: woo_prime_admin_params.nonce
			},
			success: function(response) {
				$btn.prop('disabled', false).text('🔄 Refresh Live Data');
				if (response.success && response.data) {
					var data = response.data;
					$status.css('color', 'green').text('Updated: ' + new Date().toLocaleTimeString());

					// Update Card Stats
					$('#dash-today-orders').text(data.today_orders_count || 0);
					$('#dash-week-orders').text(data.week_orders_count || 0);
					$('#dash-success-count').text(data.total_success_count || 0);
					$('#dash-failed-count').text(data.total_failed_count || 0);

					// Render Table Rows
					var logs = data.recent_transactions || [];
					if (!logs.length) {
						$tbody.html('<tr><td colspan="8" style="text-align:center; padding:20px; color:#646970;">No sync transactions found yet.</td></tr>');
						return;
					}

					var rowsHtml = '';
					window.woo_prime_transactions_cache = {};

					$.each(logs, function(i, log) {
						window.woo_prime_transactions_cache[log.name] = log;

						var statusBadge = log.status === 'Success'
							? '<span style="background:#e7f8ed; color:#00a32a; padding:3px 8px; border-radius:3px; font-weight:600; font-size:11px;">🟢 Success</span>'
							: '<span style="background:#fcf0f1; color:#d63638; padding:3px 8px; border-radius:3px; font-weight:600; font-size:11px;">🔴 Failed</span>';

						var dirBadge = log.direction === 'Incoming'
							? '<span style="color:#2271b1; font-weight:600;">📥 Incoming</span>'
							: '<span style="color:#996800; font-weight:600;">📤 Outgoing</span>';

						rowsHtml += '<tr>' +
							'<td><code>' + (log.name || '') + '</code></td>' +
							'<td><strong>' + (log.sync_type || '') + '</strong></td>' +
							'<td>' + dirBadge + '</td>' +
							'<td>' + statusBadge + '</td>' +
							'<td>' + (log.reference_doctype || '') + ': <strong>' + (log.reference_name || '-') + '</strong></td>' +
							'<td>' + (log.woo_reference_id ? 'WC-' + log.woo_reference_id : '-') + '</td>' +
							'<td><small>' + (log.formatted_time || log.creation || '') + '</small></td>' +
							'<td style="text-align:center;"><button type="button" class="button button-small view-tx-detail-btn" data-logid="' + log.name + '">View Payload</button></td>' +
						'</tr>';
					});

					$tbody.html(rowsHtml);

				} else {
					$status.css('color', 'red').text(response.data || 'Failed to load dashboard data.');
				}
			},
			error: function() {
				$btn.prop('disabled', false).text('🔄 Refresh Live Data');
				$status.css('color', 'red').text('AJAX Request Failed.');
			}
		});
	}

	if ($('.woo-prime-dashboard-page').length) {
		loadDashboardData();
	}

	$('#woo-prime-refresh-dash-btn').on('click', function(e) {
		e.preventDefault();
		loadDashboardData();
	});

	// View Transaction Details Modal
	$(document).on('click', '.view-tx-detail-btn', function() {
		var logId = $(this).data('logid');
		var log = window.woo_prime_transactions_cache ? window.woo_prime_transactions_cache[logId] : null;

		if (!log) return;

		$('#tx-modal-title').text('Transaction Payload: ' + log.name + ' (' + log.sync_type + ')');

		try {
			var reqJson = log.request_data ? JSON.stringify(JSON.parse(log.request_data), null, 2) : 'N/A';
			$('#tx-modal-request').text(reqJson);
		} catch(e) {
			$('#tx-modal-request').text(log.request_data || 'N/A');
		}

		try {
			var resJson = log.response_data ? JSON.stringify(JSON.parse(log.response_data), null, 2) : 'N/A';
			$('#tx-modal-response').text(resJson);
		} catch(e) {
			$('#tx-modal-response').text(log.response_data || 'N/A');
		}

		if (log.error_message) {
			$('#tx-modal-error').text(log.error_message);
			$('#tx-modal-error-wrap').show();
		} else {
			$('#tx-modal-error-wrap').hide();
		}

		$('#woo-prime-tx-modal').show();
	});

	$('#tx-modal-close-btn').on('click', function() {
		$('#woo-prime-tx-modal').hide();
	});
});
