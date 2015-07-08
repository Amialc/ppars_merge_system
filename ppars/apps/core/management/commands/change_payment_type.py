from django.core.management.base import BaseCommand
from ppars.apps.core.models import Customer, CompanyProfile


class Command(BaseCommand):
    '''
    change_payment_type
    '''

    def handle(self, *args, **options):
        print 'start'
        for customer in Customer.objects.filter(charge_type=Customer.CASH).exclude(charge_getaway=Customer.CASH_PREPAYMENT):
            customer.charge_getaway = Customer.CASH
            customer.save()
        print 'Done'