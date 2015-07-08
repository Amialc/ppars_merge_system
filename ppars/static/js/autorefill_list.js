/**
 * Created by eugene on 24.06.15.
 */

$(document).ready(function() {
    // Initialize table
    var table = $('#datatable').dataTable({
        "bPaginate": true,
        "bLengthChange": true,
        "bFilter": true,
        "bSort": true,
        "bInfo": true,
        "bAutoWidth": true,
        "aaSorting": [[ 3, "desc" ]],
        "aoColumnDefs" : [
            {
                "mRender": function ( data, type, row ) {
                    if (data == "True")
                        row.status_display = '<span class="fa fa-check-circle text-success"></span>';
                    else
                        row.status_display = '<span class="fa fa-minus-circle text-danger"></span>';
                    row.status_filter = data;
                    if (type === 'display') {
                        return row.status_display;
                    }
                    else if (type === 'filter') {
                        return row.status_filter;
                    }
                    return row.status_filter;

                },
                "aTargets": [8]
            },
            {
                "aTargets": [1,5,7],
                "bSortable": false
            },
            {
                "aTargets": [4,6,9,10],
                "bSearchable": false
            }
        ]
    });

    // Initialize filter by date in range
    (function(table) {
        // Init DatePicker
        $('.input-datepicker').datepicker();

        var $control = $('#datepicker-range'),
            $controls = {
                in_min: $control.find('[name="start"]'),
                in_max: $control.find('[name="end"]'),
                btn_filter: $control.find('[type="button"][name="filter"]'),
                btn_reset: $control.find('[type="button"][name="reset"]')
            },
            RangeDate = {
                Min: null,
                Max: null
            },
            iColDate = 3;

        $controls.btn_filter.on('click', function() {
            if ($controls.in_min.val() != '') {
                RangeDate.Min = new Date($controls.in_min.val());
            } else {
                RangeDate.Min = null;
            }
            if ($controls.in_max.val() != '') {
                RangeDate.Max = new Date($controls.in_max.val());
            } else {
                RangeDate.Max = null;
            }
            table.fnDraw();
        });

        $controls.btn_reset.on('click', function() {
            $controls.in_min.val('');
            $controls.in_max.val('');
            RangeDate.Min = null;
            RangeDate.Max = null;
            table.fnDraw();
        });

        $.fn.dataTableExt.afnFiltering.push(
            function(oSettings, aData, iDataIndex) {
                var iMin = RangeDate.Min;
                var iMax = RangeDate.Max;
                var iDate = new Date(aData[iColDate]);

                if (iMin == null && iMax == null) {
                    return true;
                } else
                if (iMin == null && iDate <= iMax) {
                    return true;
                } else
                if (iMin <= iDate && null == iMax) {
                    return true;
                } else
                if (iMin <= iDate && iDate <= iMax) {
                    return true;
                }
                return false;
            }
        );
    })(table);

    $('#status-filter').on('change', function () {
        table.fnFilter($(this).val(), 8);
    });
});
