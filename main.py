from pprint import pprint
from django.conf import settings
import sys
import inspect

settings.configure(
    # DEBUG=True,
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'ppars_merged_2',
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
    INSTALLED_APPS=("ppars.apps",),
    ENCRYPTED_FIELDS_KEYDIR='fieldkeys_dev',
)

from ppars.apps.accounts.models import *
from ppars.apps.card.models import *
from ppars.apps.charge.models import *
from ppars.apps.core.models import *
from ppars.apps.notification.models import *
from ppars.apps.price.models import *
from django.contrib.auth.models import User

list_models = []


def log(message):
    if settings.DEBUG:
        print message


def check_for_conflict(model, bigger_db=None):
    if bigger_db is None:
        bigger_db = 'a' if model.objects.using('a').count() > model.objects.using('b').count() else 'b'
        not_bigger = 'b' if bigger_db is 'a' else 'a'
    conflict = []
    for m in model.objects.using(bigger_db).all():
        other_m = model.objects.using(not_bigger).filter(id=m.id)
        if other_m.exists():
            log('conflict found for model %s with object %s' % (model, other_m))
            conflict.append(other_m)
    return conflict


def fk_tree(models=list_models):
    d = {}
    for model in models:
        l = list(models)
        l.remove(model)
        for model2 in l:
            try:
                test = eval(model).objects.using('a').first()
                eval('test.' + model2.lower() + '_set' + '.count()')
            except Exception, e:
                print e
            else:
                d.update({model: model2})
            #print model, model2
    pprint(d)


def main():
    log('initializing')
    for x in [m for m in inspect.getmembers(sys.modules[__name__], inspect.isclass)]:
        signature = x[1].__module__.split('.')
        if 'ppars' in signature and 'models' in signature:
            list_models.append(x[0])
            # print list_models
    # user = User.objects.using('a').all()[3]
    # print user
    # print user.customer_set.count()
    fk_tree()


if __name__ == "__main__":
    main()
