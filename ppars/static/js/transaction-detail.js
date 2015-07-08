/**
 * Created by eugene on 24.06.15.
 */

$(document).ready(function () {
    // Transaction Steps initialize
    var datatable_steps;
    (function () {
        datatable_steps = $('#steps').DataTable({
            'columns': [
                {
                    'data': 'created',
                    'title': 'Timestamp'
                },
                {
                    'data': 'operation',
                    'title': 'Step Name'
                },
                {
                    'data': 'action',
                    'title': 'Action'
                },
                {
                    'data': 'status_str',
                    'title': 'Status'
                },
                {
                    'data': 'adv_status',
                    'title': 'Advanced Status'
                }
            ],
            bSort: false,
            sDom: '<"row"<"col-md-4"l><"col-md-8"f>><"table-responsive"t><"row"<"col-md-12"p>>',
            oLanguage:
            {
                sLengthMenu: '\_MENU_',
                sSearch: '',
                sSearchPlaceholder: 'Searching...',
                oPaginate:
                {
                    sPrevious: '<',
                    sNext: '>'
                }
            }
        });
    })();

    // Show loading overlay
    function LoadingOverlay() {
        var $overlay = $('.overlay'),
            $loading = $('.loading-img');

        return {
            show: function () {
                $overlay.show();
                $loading.show();
            },
            hide: function () {
                $overlay.hide();
                $loading.hide();
            }
        };
    }

    // Scheduled monthly manual refill
    $('#schedule_monthly').on('click', function () {
        $.ajax({
            url: links.ajaxScheduleMonthly,
            success: function (result) {
                if (result['valid']) {
                    if (confirm('Scheduler refill created. Want to edit it?')) {
                        window.location = links.autorefill.replace('0', result['id']);
                    }
                } else {
                    alert(result['error']);
                }
            }
        });
    });

    // Mark paid
    $('#markPaid').on('click', function () {
        LoadingOverlay().show();
        $.ajax({
            url: links.ajaxMarkTransaction,
            data: {
                button : 'paid'
            },
            success: function() {
                updatePage();
            }
        });
    });

    // Mark completed
    $('#markCompleted').on('click', function () {
        LoadingOverlay().show();
        $.ajax({
            url: links.ajaxMarkTransaction,
            data: {
                button : 'completed'
            },
            success: function () {
                updatePage();
            }
        });
    });

    // Mark completed with pin
    // Show input and hide buttons with 'markCompleted'
    $('#markCompletedWithPin').on('click', function () {
        $('#pin-enter').show();
        $('#markCompleted, #markCompletedWithPin')
            .hide();
    });

    // Hide pin-input and show buttons with 'markCompleted'
    $('#pin-cancel').on('click', function () {
        $('#markCompleted, #markCompletedWithPin').show();
        $('#pin-enter').hide();
    });

    // Request to server 'Complete with pin'
    $('#pin-commit').on('click', function () {
        var $pin_input = $('#pin-enter-input');

        if ($pin_input.val().match(/^[0-9]+$/)) {
            LoadingOverlay().show();
            $.ajax({
                url: links.ajaxMarkTransaction,
                data: {
                    button: 'completed-with-pin',
                    pin: $pin_input.val()
                },
                success: function () {
                    $('#pin-enter').hide();
                    updatePage();
                }
            });
        } else {
            $pin_input.tooltip('show').parent().addClass('has-error');
        }
    });

    // Close transaction
    $('#closeTrans').on('click', function () {
        LoadingOverlay().show();
        $.ajax({
            url: links.ajaxMarkTransaction,
            data: {
                button : 'closed'
            },
            success: function() {
                updatePage();
            }
        });
    });

    // Close transaction and create similar
    $('#closeTransAndCrSim').on('click', function () {
        LoadingOverlay().show();
        $.ajax({
            url: links.ajaxMarkTransaction,
            data: {
                button : 'closed'
            },
            success: function() {
                window.location = links.createSimilarTransaction;
            }
        });
    });

    // Create similar transaction
    $('#CrSim').on('click', function () {
        window.location = links.createSimilarTransaction;
    });

    // Restart transaction
    $('#restartTrans').on('click', function () {
        LoadingOverlay().show();
        $.ajax({
            url: links.ajaxMarkTransaction,
            data: {
                button : 'restarted'
            },
            success: function () {
                setTimeout(function () {
                    updatePage();
                }, 2000);
            }
        });
    });

    // Restart prerefill
    $('#restartPrerefill').on('click', function () {
        LoadingOverlay().show();
        $.ajax({
            url: links.ajaxMarkTransaction,
            data: {
                button : 'prerefill_restart'
            },
            success: function () {
                setTimeout(function () {
                    updatePage();
                }, 2000);
            }
        });
    });

    // Updating information
    (function () {
        Updater = (function Updater () {
            var looper;
            var intervalUpdate = 0;
            var userIntervalUpdate = 0;
            var run = false;

            var CheckUpdate = function () {
                LoadingOverlay().show();
                updatePage();
                if (run) {
                    clearInterval(looper);
                    CheckState();
                    if (intervalUpdate) {
                        looper = setInterval(CheckUpdate, intervalUpdate);
                    }
                } else {
                    clearInterval(looper);
                }
            };

            var CheckState = function () {
                switch (g_data.state) {
                    case 'P':
                        intervalUpdate = 30000;
                        break;
                    case 'E':
                        intervalUpdate = 0;
                        break;
                    case 'Q':
                        intervalUpdate = 30000;
                        break;
                    case 'R':
                        if (g_data.status == 'E') {
                            intervalUpdate = userIntervalUpdate;
                        } else {
                            intervalUpdate = 300000;
                        }
                        break;
                    case 'I':
                        intervalUpdate = 0;
                        break;
                    case 'C':
                        intervalUpdate = 0;
                        break;
                    default :
                        intervalUpdate = 0;
                        break;
                }
            };

            var that = this;
            return {
                run: function () {
                    run = true;
                    CheckUpdate();
                    return that;
                },
                stop: function () {
                    run = false;
                    return that;
                },
                interval: function (interval) {
                    userIntervalUpdate = interval;
                    return that;
                }
            };
        })().run();
    })();

    function updatePage() {
        $.ajax({
            url: links.ajaxTransaction,
            type: 'GET',
            dataType: 'JSON',
            contentType: 'JSON',
            success: function(result) {
                var transaction = result['transaction'];
                var steps = result['steps'];

                // Load information in right panel
                $('#triggered_by').html(transaction['triggered_by']);
                $('#phone_number').html(transaction['phone_number']);
                $('#plan').html(transaction['plan']);
                $('#profit').html(transaction['profit']);
                $('#refill_type').html(transaction['refill_type']);
                $('#pin').html(transaction['pin']);
                $('#state').html(transaction['state_str']);
                $('#status').html(transaction['status_str']);
                if (transaction['state'] == 'R' || transaction['state'] == 'Q') {
                    $('#closeTrans').show();
                    $('#closeTransAndCrSim').show();
                    $('#CrSim').hide();
                } else {
                    $('#closeTrans').hide();
                    $('#closeTransAndCrSim').hide();
                    $('#CrSim').show();
                }
                if (transaction['state'] == 'I' || transaction['state'] == 'C') {
                    $('#restartTrans').show();
                } else {
                    $('#restartTrans').hide();
                }
                if (transaction['state'] == 'C' &&
                    transaction['status'] == 'E' &&
                    transaction['current_step'] != 'recharge_phone' &&
                    transaction['current_step'] != 'send_notifications' &&
                    transaction['autorefill_trigger'] != 'MN') {
                    $('#restartPrerefill').show();
                } else {
                    $('#restartPrerefill').hide();
                }
                if (transaction['paid']) {
                    $('#pstatus-indicator')
                        .removeAttr('class')
                        .addClass('fa fa-check-circle text-success')
                        .show();
                    $('#markPaid')
                        .hide();
                } else {
                    if (g_data.company != transaction['company']) {
                        $('#pstatus-indicator')
                            .removeAttr('class')
                            .addClass('fa fa-minus-circle text-danger')
                            .show();
                        $('#markPaid')
                            .hide();
                    } else {
                        $('#pstatus-indicator')
                            .removeAttr('class')
                            .addClass('fa fa-minus-circle text-danger')
                            .show();
                        $('#markPaid')
                            .show();
                    }
                }
                if (transaction['completed']) {
                    $('#tstatus-indicator')
                        .removeAttr('class')
                        .addClass('fa fa-check-circle text-success')
                        .show();
                    $('#markCompleted, #markCompletedWithPin')
                        .hide();
                } else {
                    if (g_data.company != transaction['company']) {
                        $('#tstatus-indicator')
                            .removeAttr('class')
                            .addClass('fa fa-minus-circle text-danger')
                            .show();
                        $('#markCompleted, #markCompletedWithPin')
                            .hide();
                    } else {
                        $('#tstatus-indicator')
                            .removeAttr('class')
                            .addClass('fa fa-minus-circle text-danger')
                            .show();
                        $('#markCompleted, #markCompletedWithPin')
                            .show();
                    }
                }

                if (transaction['status'] == 'E') {
                    $('#status_row').addClass('danger');
                } else {
                    $('#status_row').removeClass('danger');
                }
                if (transaction['adv_status']) {
                    $('#adv_status').html(transaction['adv_status']);
                }
                $('#started').html(transaction['started']);
                if (transaction['state'] == 'C') {
                    $('#ended').html(transaction['ended']);
                }
                g_data.state = transaction['state'];
                g_data.status = transaction['status'];
                datatable_steps.clear();
                for (var i = 0; i < steps.length; i++) {
                    $(datatable_steps.row.add(steps[i]).node())
                        .addClass(steps[i]['status'] == 'E' ? 'danger' : '');

                    if (steps[i]['adv_status']) {
                        if (steps[i]['adv_status'].indexOf('Use charge') >= 0) {
                            $('#pustatus').html(steps[i]['adv_status']);
                        }
                    }
                }
                datatable_steps.draw().page('last').draw(false);
                LoadingOverlay().hide();
            }
        });
    }
});