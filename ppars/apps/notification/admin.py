from django.contrib import admin

from ppars.apps.notification.models import SpamMessage, SmsEmailGateway


class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        'subject', 'send_with', 'status', 'created'
    ]
    list_filter = ('send_with', 'status')


# admin.site.register(Notification, NotificationAdmin)
admin.site.register(SpamMessage)
admin.site.register(SmsEmailGateway)