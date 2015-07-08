import decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.urlresolvers import reverse
from ppars.apps.charge.models import Charge
from ppars.apps.core.models import CompanyProfile


class Command(BaseCommand):
    '''
    used_zero
    '''

    def handle(self, *args, **options):
        print "start"
        for company in CompanyProfile.objects.filter(superuser_profile=False):
            print company.company_name
            for charge in Charge.objects.filter(company=company, status=Charge.SUCCESS, used=True, summ=decimal.Decimal(0.0)):
                print '%s%s' % (settings.SITE_DOMAIN, reverse('charge_detail', args=[charge.id]))
        print 'end'