import os
from django.conf import settings
import sys

settings.configure(
    DEBUG=True,
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'ppars_merged',
            'USER': 'root',
            'PASSWORD': '',
            'HOST': '127.0.0.1',
        },
        'a': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'ppars_a_merge',
            'USER': 'root',
            'PASSWORD': '',
            'HOST': '127.0.0.1',
        },
        'b': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'ppars_b_merge',
            'USER': 'root',
            'PASSWORD': '',
            'HOST': '127.0.0.1', }
    },
    INSTALLED_APPS=("ppars",),
    ENCRYPTED_FIELDS_KEYDIR='fieldkeys_dev',
)

from ppars.apps.accounts.models import *
from ppars.apps.card.models import *
from ppars.apps.charge.models import *
from ppars.apps.core.models import *
from ppars.apps.notification.models import *
from ppars.apps.price.models import *


def log(message):
    if settings.DEBUG:
        print message

def main():
    conflict = []
    log('initializing')
    customers_a = Customer.objects.using('a').all()
    customers_b = Customer.objects.using('b').all()
    bigger_db = 'a' if customers_a.count() > customers_b.count() else 'b'
    not_bigger = 'b' if bigger_db is 'a' else 'b'
    Customer.objects.all().delete() #cleaning default database
    for customer in Customer.objects.using(bigger_db).all():
        customer_other = Customer.objects.using(not_bigger).filter(id=customer.id)
        if customer_other.exists():
            log("Found conflict with %s" % customer_other)
            conflict.append(customer_other)
    print len(conflict)


if __name__ == "__main__":
    main()
