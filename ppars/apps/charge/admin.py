from django.contrib import admin
from ppars.apps.charge.models import Charge, TransactionCharge, ChargeStep

admin.site.register(Charge)
admin.site.register(TransactionCharge)
admin.site.register(ChargeStep)
