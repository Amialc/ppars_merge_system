import sys
from defaults import INSTALLED_APPS
import authorize
from .util import root

DEBUG = True

TEMPLATE_DEBUG = DEBUG

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'ppars_merged',
        'USER': 'root',
        'PASSWORD': '',
        'HOST': '127.0.0.1',
    },
    # 'mirror': {
    # 'ENGINE': 'django.db.backends.mysql',
    #     'NAME': 'pp3',
    #     'USER': 'root',
    #     'PASSWORD': '123456',
    #     'HOST': '127.0.0.1',
    # }
}

# Change TEST to PRODUCTION in production
AUTHORIZE_ENVIRONMENT = authorize.Environment.TEST

# for live server use 'www' for test server use 'sandbox'
USAEPAY_WSDL = "https://sandbox.usaepay.com/soap/gate/CD12CD14/usaepay.wsdl"

TEST_MODE = True

# CACHES = {
#     "default": {
#         "BACKEND": "django_redis.cache.RedisCache",
#         "LOCATION": "redis://127.0.0.1:6379/3",
#         "OPTIONS": {
#             "CLIENT_CLASS": "django_redis.client.DefaultClient",
#         }
#     }
# }

INSTALLED_APPS += (
    'debug_toolbar',
)

CELERY_ALWAYS_EAGER = True
CELERY_EAGER_PROPAGATES_EXCEPTIONS = True

SITE_DOMAIN = 'http://127.0.0.1:8000'

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

ENCRYPTED_FIELDS_KEYDIR = root('..', 'fieldkeys_dev')

print(__file__, 'local settings', 'loaded')