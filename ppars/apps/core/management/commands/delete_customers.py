from django.core.management.base import BaseCommand
from ppars.apps.core.models import Customer, CompanyProfile


class Command(BaseCommand):
    '''
    delete_customers
    '''

    def handle(self, *args, **options):
        company = CompanyProfile.objects.get(id=6317690062897158)
        for customer in Customer.objects.filter(company=company):
            customer.delete()
        print "done"