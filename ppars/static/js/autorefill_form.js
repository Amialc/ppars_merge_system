/**
 * Created by eugene on 19.06.15.
 */

$(document).ready(function(){
    /*
    * Fix bug, when initializing page,
    * call event onChange by selectize (Carrier) on loading and selecting default data
    */
    var onInitializingCarriers = true;
    /*
    *
    */

    // Set up datepicker
    $('.date-picker .input-group.date').datepicker({
        startDate: 'today',
        format: 'mm/dd/yyyy',
        todayBtn: 'linked'
    });

    // Tooltip on 'Schedule'
    $('[data-toggle="popover"]').popover({
        trigger: 'manual',
        animate: false
    });
    $('.schedule_select').on('change', function() {
        var $select = $(this),
            msg = 'Select schedule';

        switch($select.val()) {
            case 'MN':
                msg = 'Used for Red Pocket & Page Plus';
                break;
            case 'MD':
                msg = 'Used for rtr';
                break;
            case '1201AM':
                msg = 'Used for Red Pocket when second bucket is enabled';
                break;
        }

        $select.attr('data-content', msg);
        $('[data-toggle="popover"]').popover('show');
    });

    // Enabled / Disabled SMS-number by checkbox
    $('#id_pre_refill_sms')
        .on('ifChecked', function() {
            $('#id_pre_refill_sms_number').prop('disabled', false);
        })
        .on('ifUnchecked', function() {
            $('#id_pre_refill_sms_number').prop('disabled', true);
        });

    // Validate sms-number
    $('#id_pre_refill_sms_number')
        .on('input', function() {
            this.setCustomValidity(
              $(this).val().match(/^[0-9]{10,10}$/) ?
                  '' : 'The number should only consist of 10 digits!'
            );
        })
        .on('keypress', function(event) {
            var $this = $(this);

            if ($this.val().length > 10) {
                event.preventDefault();
            }
        });

    // Temperately variable for last plan used
    var tmpPlanValue = null;
    // Copy to clipboard
    ZeroClipboard.config({
        swfPath: links.ZeroClipboard
    });
    new ZeroClipboard($('#copy_phone_number'));

    // Initialize selectizes
    // block Customer
    $('#id_customer').selectize({
        sortField: 'text',
        onChange: update_$phone_number,
        onInitialize: function() {
            var that = this;
            var callback = GETparam('cid', function() {
                if (this.check()) {
                    if (!this.used()) {
                        that.setValue(this.val());
                        this.used(true);
                        return true;
                    }
                }
                return false;
            });

            if (!callback) {
                this.setValue($('#hidden_customer').val());
            }
        }
    });

    // block Phone Number
    var $phone_number = $('#id_phone_number').selectize({
        create: function(input) {
                $.ajax({
                    type: 'GET',
                    url: links.ajaxAddPhoneNumber,
                    data: {
                        customer: $('#id_customer').val(),
                        number: input
                    },
                    dataType: 'json',
                    success: function (data) {
                        $('#help_phone_number').text(data)
                    }
                });
            return {
                value: input,
                text: input
            }
        },
        onChange: function(value) {
            $('#copy_phone_number').attr('data-clipboard-text', value);
        },
        onLoad: function() {
            var that = this;
            var callback = GETparam('ph', function() {
                if (this.check()) {
                    if (!this.used()) {
                        that.setValue(this.val());
                        this.used(true);
                        return true;
                    }
                }
                return false;
            });
            if (!callback) {
                that.setValue($('#hidden_phone_number').val());
            }
        }
    });
    function update_$phone_number(value) {
        $.ajax({
            type: 'GET',
            url: links.ajaxPhoneNumber,
            data: {
                id: value
            },
            dataType: 'json',
            success: function(data) {
                var selectize = $phone_number[0].selectize;

                selectize.clear();
                selectize.clearOptions();

                selectize.load(function(callback) {
                    callback(data);
                });
            }
        });
    }

    GETparam('crt_from', function() {
        if (this.check()) {
            if (!this.used()) {
                $('#id_notes').val('Schedule created from transaction ' + this.val());
                this.used(true);
            }
        }
    });

    // block Carriers
    var $carriers = $('#id_carrier').selectize({
        valueField: 'pk',
        labelField: 'name',
        searchField: 'name',
        create: false,
        preload: true,
        render: {
            option: function(item, escape) {
                return '<div>' +
                    '<img src="/static/img/' + escape(item['name_slug']) + '.jpg" style="width:36px;" ><span class="title"> ' + escape(item['name']) + '</span>' +
                    '</div>';
            }
        },
        onChange: function(value) {
            update_$plans(value);
            if (!onInitializingCarriers) {
                $.ajax({
                    url: links.ajaxCarrier,
                    type: 'GET',
                    dataType: 'JSON',
                    data: {
                        'carid': value
                    },
                    success: function (rs) {
                        $('#id_schedule').val(rs['default_time']);
                    }
                });
            }
        },
        load: function(query, callback) {
            if(query.length) return callback();

            $.ajax({
                type: 'GET',
                url: links.ajaxCarriers,
                dataType: 'json',
                success: function(data) {
                    callback(data);
                }
            });
        },
        onLoad: function() {
            var that = this;
            var callback = GETparam('carid', function() {
                if (this.check()) {
                    if (!this.used()) {
                        that.setValue(this.val());
                        this.used(true);
                        return true;
                    }
                }
                return false;
            });
            if (!callback) {
                callback = GETparam('lp', function () {
                    //setting last used plan for refill when coming from button Recharge With Last Plan from search tab
                    if (this.check() && !callback) {
                        if (!this.used() && this.val() == 't') {
                            this.used(true);
                            load_last_used_plan();
                            return true;
                        }
                    }
                    return false;
                });
            }
            if (!callback) {
                this.setValue($('#hidden_carrier').val());
            }
            onInitializingCarriers = false;
        }
    });

    // block Plans
    var $plans = $('#id_plan').selectize({
        valueField: 'pk',
        labelField: 'id',
        searchField: 'id',
        create: false,
        render: {
            option: function(item, escape) {
                return '<div>' +
                    '<span class="title">' +
                    '<span class="name">' + escape(item.id) + '</span>' +
                    '</span>' +
                    '<span class="description">' + escape(item.name) + '</span>' +
                    '<ul class="meta">' +
                    '<li> Cost: ' + escape(item.cost) + '</li>' +
                    '<li> Type: ' + escape(item.type) + '</li>' +
                    '<li>' + escape(item.available) + ' for use</li>' +
                    '</ul>' +
                    '</div>';
            }
        },
        onChange: function(value) {
            if (this.options[value] && !tmpPlanValue) {
                if (this.options[value]['type'].indexOf('Top-Up') >= 0) {
                    $('[value="GP"]').css('display', 'none');
                    $('#id_refill_type').val('FR');
                } else {
                    $('[valu-e="GP"]').css('display', 'block');
                    $('#id_refill_type').val('');
                }
            }
        },
        onLoad: function() {
            var that = this;
            var callback = false;

            if (tmpPlanValue) {
                this.setValue(tmpPlanValue);
                tmpPlanValue = null;
            } else
            if (
                callback = GETparam('pid', function() { /* START: GETparam */
                    if (this.check()) {
                        if (!this.used()) {
                            that.setValue(this.val());
                            this.used(true);
                            return true;
                        }
                    }
                    return false;
                }) /* END: GETparam */
            ) {
                GETparam('sched', function() {
                    if (this.check()) {
                        if (!this.used()) {
                            $('#id_schedule').val(this.val());
                            this.used(true);
                        }
                    }
                });
                GETparam('ppsms', function() {
                    if (this.check()) {
                        if (!this.used()) {
                            $('#id_pre_refill_sms').val(this.val());
                            this.used(true);
                        }
                    }
                });
                GETparam('ppsmsn', function() {
                    if (this.check()) {
                        if (!this.used()) {
                            $('#id_pre_refill_sms_number').val(this.val());
                            this.used(true);
                        }
                    }
                });
            } else
            if (!callback) {
                this.setValue($('#hidden_plan').val());
            }
        }
    });
    function update_$plans(value) {
        $.ajax({
            type: 'GET',
            url: links.ajaxCarrierPlans,
            data: {
                id: value
            },
            dataType: 'json',
            success: function(data) {
                var selectize = $plans[0].selectize;

                selectize.clear();
                selectize.clearOptions();

                selectize.load(function(callback) {
                    callback(data);
                });
            }
        });
    }

    function load_last_used_plan() {
        $.ajax({
            type: "GET",
            url: links.ajaxLastTransData,
            data: {
                phone_number: $phone_number.val()
            },
            dataType: "json",
            success: function (data) {
                if (data['exist']) {
                    $('#last_used_plan').text('Load last used plan');

                    var selectize_$carriers = $carriers[0].selectize;

                    selectize_$carriers.setValue(data['carrier']);
                    tmpPlanValue = data['plan'];
                } else {
                    $('#last_used_plan').text('No transactions for this number');
                }
            }
        });
    }
    // Load last used plan on click
    $('#last_used_plan').on('click', load_last_used_plan);
});

function SkipRefill(id){
        $.ajax({
        type: 'GET',
        url: '/ajax_skip_next_refill/',
        data: {
            'id': id
        },
        dataType: 'json',
        success: function (data) {
            if (data['valid'])
            {
                $this = $('#skip_next_refill');
                $this.text('Next refill will be skipped!');
                $this.addClass('disabled btn-success');
                $('#id_renewal_date').val(data['renewal_date']);
                $('#id_renewal_end_date').val(data['end_renewal_date']);
            }
        }
        });
    };