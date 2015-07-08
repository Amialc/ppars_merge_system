from django.contrib import admin

from ppars.apps.core.models import Plan, Carrier, UserProfile, AutoRefill, ImportLog,\
    CompanyProfile, PhoneNumber, Log, \
    CommandLog, Customer, CaptchaLogs, CarrierAdmin, UnusedPin, Transaction, \
    PlanDiscount, TransactionStep, ConfirmDP, PinReport, News, TransactionError
from .resources import PlanAdmin,  UserProfileAdmin,\
    CompanyProfileAdmin, LogAdmin, \
    CarrierResourceAdmin, CarrierAdminResourceAdmin, \
    PlanDiscountAdmin,  \
    UnusedPinAdmin,  CaptchaLogsAdmin, ImportLogAdmin, \
    CommandLogAdmin  #TransactionAdmin, TransactionStepAdmin, PhoneNumberAdmin, CustomerAdmin,
import tasks


class AutoRefillAdmin(admin.ModelAdmin):
    list_display = [
        'pk', 'customer', 'phone_number', 'plan', 'pin', 'refill_type',
        'renewal_date', 'last_renewal_status', 'last_renewal_date', 'schedule',
        'trigger', 'enabled',
    ]
    list_filter = ('schedule', 'company', 'trigger', 'refill_type', 'enabled',)

    date_hierarchy = 'renewal_date'


class TransactionStepAdmin(admin.ModelAdmin):
    list_display = [
        'pk', 'transaction', 'operation', 'action', 'created',
    ]


def restart_transaction(modeladmin, request, queryset):
    for obj in queryset:
        tasks.queue_refill.delay(obj.id)


restart_transaction.short_description = "restart transaction"


class TransactionAdmin(admin.ModelAdmin):
    list_display = [
        'pk', 'autorefill', 'state',  'paid', 'completed', 'pin_error',
        'status', 'current_step', 'retry_count', 'locked', 'triggered_by',
        'started', 'ended'
    ]

    list_filter = ('company', 'locked', 'paid', 'state', 'status', 'completed', 'pin_error')

    actions = [restart_transaction]
    # todo: uncomment
    # date_hierarchy = 'started'


class PhoneNumberAdmin(admin.ModelAdmin):
    list_display = ['number', 'customer']
    list_filter = ['company']
    search_fields = ['number', 'customer__first_name', 'customer__last_name']


class CustomerAdmin(admin.ModelAdmin):
    list_filter = ['company']


# admin.site.unregister(User)
# admin.site.register(User, UserAdmin)
admin.site.register(CompanyProfile, CompanyProfileAdmin)
admin.site.register(UserProfile, UserProfileAdmin)
admin.site.register(Customer, CustomerAdmin)
admin.site.register(PhoneNumber, PhoneNumberAdmin)
admin.site.register(Carrier, CarrierResourceAdmin)
admin.site.register(CarrierAdmin, CarrierAdminResourceAdmin)
admin.site.register(Plan, PlanAdmin)
admin.site.register(PlanDiscount, PlanDiscountAdmin)
admin.site.register(AutoRefill, AutoRefillAdmin)
admin.site.register(Transaction, TransactionAdmin)
admin.site.register(TransactionStep, TransactionStepAdmin)
admin.site.register(UnusedPin, UnusedPinAdmin)
admin.site.register(Log, LogAdmin)
admin.site.register(CaptchaLogs, CaptchaLogsAdmin)
admin.site.register(CommandLog, CommandLogAdmin)
admin.site.register(ConfirmDP)
admin.site.register(ImportLog, ImportLogAdmin)
admin.site.register(PinReport)
admin.site.register(News)
admin.site.register(TransactionError)
