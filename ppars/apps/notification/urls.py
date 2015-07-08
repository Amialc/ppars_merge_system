from django.conf.urls import patterns, url
from django.contrib.auth.decorators import login_required

from ppars.apps.notification.views import SpamMessageCreate, CustomPreChargeMessageDetail

urlpatterns = patterns('',
    url(r'^create/$', login_required(SpamMessageCreate.as_view()), name='sms_create'),
    url(r'^precharge/$', login_required(CustomPreChargeMessageDetail.as_view()), name='custom_message'),

)
