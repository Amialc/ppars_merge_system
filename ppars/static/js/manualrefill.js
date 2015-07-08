/**
 * Created by eugene on 17.06.15.
 *
 * Using GETparams.js !!!
 */

$(document).ready(function(){
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
            GETparam('cid', function() {
                if (this.check()) {
                    if (!this.used()) {
                        that.setValue(this.val());
                        this.used(true);
                    }
                }
            });
        }
    });

    GETparam('crt_from', function() {
        if (this.check()) {
            if (!this.used()) {console.log(this.val());
                $('[name="created-from"]').val(this.val());
                this.used(true);
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

            GETparam('ph', function() {
                if (this.check()) {
                    if (!this.used()) {
                        that.setValue(this.val());
                        this.used(true);
                    }
                } else {
                    that.setValue($('#hidden_phone_number').val());
                }
            });
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
        onChange: update_$plans,
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
                    $('[value="GP"]').css('display', 'block');
                }
            }
        },
        onLoad: function() {
            var that = this;

            if (tmpPlanValue) {
                this.setValue(tmpPlanValue);
                tmpPlanValue = null;
            } else
            if (
                GETparam('pid', function() { /* START: GETparam */
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
                GETparam('rftype', function() {
                    if (this.check()) {
                        if (this.used()) {
                            $('#id_refill_type').val(this.val());
                            this.used(true);
                        }
                    }
                });
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
                    $('#id_refill_type').val(data["refill_type"]);
                    $('#id_pin').trigger('change');
                } else {
                    $('#last_used_plan').text('No transactions for this number');
                }
            }
        });
    }

    // Load last used plan on click
    $('#last_used_plan').on('click', load_last_used_plan);

    // Get local utcOffset (timezone) for datetime_refill
    (function () {
        $('#datetimepicker').datetimepicker({
            minDate: moment(),
            format: "DD MMMM YYYY HH:mm"
        });

        $('[name="datetime_refill_tzone"]').val(moment().utcOffset());
    })();

    // If pin is entered
    (function() {
        $('#id_pin')
            .on('change', function() {
                if (
                    $(this).val() === ''
                ) {
                    $('[value="GP"]').css('display', 'block');
                } else {
                    $('[value="GP"]').css('display', 'none');
                    $('#id_refill_type').val('FR');
                }
            });
    })();
});
