from pprint import pprint
from django.conf import settings
import sys
import inspect

settings.configure(
    DEBUG=True,
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


def log(message):
    if settings.DEBUG:
        print message


def check_for_conflict(model, bigger_db=None):
    if bigger_db is None:
        bigger_db = 'a' if model.objects.using('a').count() > model.objects.using('b').count() else 'b'
        not_bigger = 'b' if bigger_db is 'a' else 'a'
    conflict = []
    for m in model.objects.using(not_bigger).all():
        other_m = model.objects.using(bigger_db).filter(id=m.id)
        if other_m.exists():
            log('conflict found for model %s with object %s' % (model, other_m))
            temp = []
            for other in other_m:
                temp.append(other)
            temp.append(m)
            conflict.append(temp)
    return conflict


def fk_tree(models):
    d = {}
    for model in models:
        l = list(models)
        l.remove(model)
        for model2 in l:
            try:
                test = eval(model).objects.using('a').first()
                eval('test.' + model2.lower() + '_set.count()')
            except Exception, e:
                log(e)
            else:
                if model in d:
                    d[model].append(model2)
                else:
                    d.update({model: [model2]})
    pprint(d)
    return d

def get_related_objects(object,db, fk):
    related = []
    for clas in fk[str(object.__class__).split("'")[1].split('.')[-1]]:
        log(clas)
        try:
            related.append(list(eval('object.' + clas.lower() + '_set.all()')))
        except Exception, e:
            log(e)
    print related
    return related


def merger(list_of_models, fk, bigger_db=None):
    conflict = []
    for model in list_of_models:
        model.objects.using('default').all().delete()
        if bigger_db is None:
            bigger_db = 'a' if model.objects.using('a').count() > model.objects.using('b').count() else 'b'
            not_bigger = 'b' if bigger_db is 'a' else 'a'
        conflict = []
        for m in model.objects.using(not_bigger).all():
            other_m = model.objects.using(bigger_db).filter(id=m.id)
            if other_m.exists():
                log('conflict found for model %s with object %s' % (model, other_m))
                conflict.append(m.id)
            else:
                m.save(using='default')
                last_id = m.id
        for id in conflict:
            model_a = model.objects.using('a').get(id=id)
            model_b = model.objects.using('b').get(id=id)
    return conflict


def main():
    log('initializing')
    list_models = []
    for clas in [member for member in inspect.getmembers(sys.modules[__name__], inspect.isclass)]:
        signature = clas[1].__module__.split('.')
        if 'ppars' in signature and 'models' in signature:
            list_models.append(clas[0])
            # print list_models
    get_related_objects(Customer.objects.using('a').all()[0], 'a', fk_tree(list_models))
    #merger(list_models, fk_tree(list_models))


if __name__ == "__main__":
    main()
