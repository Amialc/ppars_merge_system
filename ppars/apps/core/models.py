import decimal
import datetime
import hashlib
import time
import logging
import traceback
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.signals import post_save, pre_save, pre_delete
from django.db.models import signals
from django.dispatch import receiver
import pytz
from twilio.rest import TwilioRestClient
from django.db.models import Q

import authorize
from asana import asana
from pytz import timezone
from datetime import timedelta
from pytz import utc
from ext_lib import url_with_querystring
from pysimplesoap.simplexml import SimpleXMLElement
from pysimplesoap.client import SoapClient
from gadjo.requestprovider.signals import get_request

import fields

logger = logging.getLogger('ppars')


def _get_user_profile(user):
    profile = user.profile
    return profile


def _get_company_profile(user):
    company = user.profile.company
    return company

User.get_company_profile = _get_company_profile
User.get_user_profile = _get_user_profile


class CompanyProfile(models.Model):
    """
    This model is a company profile model.
    It is use for all user in one company
    """
    DONT_SEND = 'NO'
    MAIL = 'EM'
    SMS = 'SM'
    SMS_EMAIL = 'SE'
    SEND_STATUS_CHOICES = (
        (DONT_SEND, "Don't Send"),
        (SMS_EMAIL, 'SMS via Email'),
        (SMS, 'via Twilio SMS'),
        (MAIL, 'via Email'),
    )
    DOLLAR_TYPE_CHOICES = (
        ('A', 'API'),
        ('S', 'Web Site'),
    )
    DOLLARPHONE = 'DP'
    AUTHORIZE = 'A'
    USAEPAY = 'U'
    CASH_PREPAYMENT = 'CP'
    CASH = 'CA'
    CCCHARGE_TYPE_CHOICES = (
        (AUTHORIZE, 'Authorize'),
        (USAEPAY, 'USAePay'),
        (DOLLARPHONE, 'DollarPhone'),
        # (CASH_PREPAYMENT, 'Cash(PrePayment)'),
        # (CASH, 'Cash'),
    )

    PPR_TYPE_CHOICES = (
        ('TW', 'Phone(Twilio)'),
        ('CP', 'Website'),
    )
    id = fields.BigAutoField(primary_key=True)
    # Link to the user model
    # user = models.ForeignKey(User, related_name='profile', unique=False, blank=True, null=True)

    updated = models.BooleanField(default=False)
    superuser_profile = models.BooleanField(default=False)

    # General
    company_name = models.CharField(max_length=40, null=True, blank=True)
    email_id = models.EmailField(null=True, blank=True)
    email_success = models.BooleanField(default=False)
    precharge_failed_email = models.BooleanField(default=True)
    block_duplicate_schedule = models.BooleanField(default=True)
    block_duplicate_phone_number = models.BooleanField(default=True)
    pin_error = models.BooleanField(default=False)
    short_retry_limit = models.IntegerField(validators=[MinValueValidator(0),MaxValueValidator(9)], null=True, blank=True)
    short_retry_interval = models.IntegerField(validators=[MinValueValidator(0),MaxValueValidator(1200)], null=True, blank=True)
    long_retry_limit = models.IntegerField(validators=[MinValueValidator(0),MaxValueValidator(9)], null=True, blank=True)
    long_retry_interval = models.IntegerField(validators=[MinValueValidator(0),MaxValueValidator(1200)], null=True, blank=True)
    customer_limit = models.IntegerField(validators=[MinValueValidator(0)], null=True, blank=True, default=0)
    send_pin_prerefill = models.CharField(max_length=2, choices=SEND_STATUS_CHOICES, default=DONT_SEND)
    able_change_send_pin_prerefill = models.BooleanField(default=False)
    insufficient_funds_notification = models.BooleanField(default=False)
    show_updates = models.BooleanField(default=False, editable=False)
    email_sales_agent = models.EmailField(null=True, blank=True)

    #Tax
    tax = models.DecimalField(max_digits=7, decimal_places=4, default=decimal.Decimal(0.0))

    # Twilio
    twilio_number = models.CharField(max_length=10, null=True, blank=True)
    twilio_sid = models.CharField(max_length=500, null=True, blank=True)
    twilio_auth_token = models.CharField(max_length=500, null=True, blank=True)

    # Death By Captcha
    deathbycaptcha_user = models.CharField(max_length=100, blank=True, null=True)
    deathbycaptcha_pass = models.CharField(max_length=100, blank=True, null=True)
    deathbycaptcha_email_balance = models.IntegerField(max_length=10, null=True, blank=True, default=70)
    deathbycaptcha_count = models.IntegerField(max_length=10, null=True, blank=True, default=5000)
    deathbycaptcha_current_count = models.IntegerField(max_length=10, null=True, blank=True, default=0)
    deathbycaptcha_emailed = models.BooleanField(default=True)

    # Page Plus Recharge
    pageplus_refillmethod = models.CharField(max_length=3, choices=PPR_TYPE_CHOICES, default='TW', blank=True, null=True)

    # Dollar Phone
    dollar_type = models.CharField(max_length=3, choices=DOLLAR_TYPE_CHOICES, blank=True, null=True)
    dollar_user = models.CharField(max_length=100, blank=True, null=True)
    dollar_pass = models.CharField(max_length=100, blank=True, null=True)

    #Mandrill
    mandrill_key = models.CharField(max_length=500, null=True, blank=True)
    mandrill_email = models.EmailField(null=True, blank=True)

    #authorize.net
    authorize_api_login_id = models.CharField(max_length=100, null=True, blank=True)
    authorize_transaction_key = models.CharField(max_length=100, null=True, blank=True)

    # Credit Card Charging getaway type
    cccharge_type = models.CharField(max_length=3, choices=CCCHARGE_TYPE_CHOICES, blank=True, null=True)
    unused_charge_notification = models.BooleanField(default=True)
    authorize_precharge_days = models.IntegerField(validators=[MinValueValidator(0),MaxValueValidator(5)], null=True, blank=True)
    precharge_notification_type = models.CharField(max_length=2, choices=SEND_STATUS_CHOICES, default=SMS, blank=True)


    # USAePay
    usaepay_source_key = models.CharField(max_length=100, null=True, blank=True)
    usaepay_pin = models.CharField(max_length=100, null=True, blank=True)
    usaepay_username = models.CharField(max_length=100, blank=True, null=True)
    usaepay_password = models.CharField(max_length=100, blank=True, null=True)

    #sellercloud
    use_sellercloud = models.BooleanField(default=False)
    sc_company_id = models.CharField(max_length=100, null=True, blank=True)
    sc_password = models.CharField(max_length=100, blank=True, null=True)
    sc_email = models.EmailField(null=True, blank=True)

    #asana
    use_asana = models.BooleanField(default=False)
    asana_api_key = models.CharField(max_length=100, null=True, blank=True)
    asana_workspace = models.CharField(max_length=100, null=True, blank=True)
    asana_project_name = models.CharField(max_length=100, null=True, blank=True)
    asana_user = models.CharField(max_length=100, null=True, blank=True)
    deposit_amount_notification = models.BooleanField(default=True)

    def __unicode__(self):
        return u'%s:[%s]' % (self.company_name, self.id)

    def authorize_authorization(self):
        authorize.Configuration.configure(
            settings.AUTHORIZE_ENVIRONMENT,
            self.authorize_api_login_id,
            self.authorize_transaction_key
        )

    def get_absolute_url(self):
        return reverse('profile')

    def check_available_customer_create(self):
        if self.customer_limit == 0:
            return True
        if Customer.objects.filter(company=self).count() >= self.customer_limit:
            return False
        else:
            return True

    def usaepay_authorization(self):
        seed = time.time()
        clear = '%s%s%s' % (self.usaepay_source_key, seed, self.usaepay_pin)
        m = hashlib.sha1()
        m.update(clear)
        token = {
            u'ClientIP':'192.168.0.1',
            u'PinHash': {
                u'HashValue': m.hexdigest(),
                u'Seed': seed,
                u'Type': 'sha1'
            },
            u'SourceKey': self.usaepay_source_key
        }
        return token

    def sellingprices_amount_for_week(self):
        '''
        :return: selling prices amount for dashboard
        '''
        result = [0, 0, 0, 0, 0, 0, 0]  # week list
        from datetime import timedelta
        today = datetime.datetime.now(timezone('US/Eastern')).date()
        for plan in Plan.objects.all():
            unused_pins_amount = UnusedPin.objects.filter(company=self, plan=plan, used=False).count()
            for autorefill in AutoRefill.objects.filter(renewal_date__range=(today, today+timedelta(days=6)), plan=plan,
                                                        company=self, enabled=True):
                appeared = 0  # counting how many times scheduled refill will be triggered this week
                autorefill_week = [0, 0, 0, 0, 0, 0, 0]
                if autorefill.renewal_date:
                    for i in range(7):
                        if autorefill.renewal_interval:
                            if today+timedelta(days=i) \
                                    == autorefill.renewal_date+timedelta(days=autorefill.renewal_interval*appeared) \
                                    <= today+timedelta(days=6):
                                appeared += 1
                                if unused_pins_amount < 1:
                                    autorefill_week[i] = autorefill.calculate_cost_and_tax()[0]
                                else:
                                    unused_pins_amount -= 1
                                continue
                        if autorefill.plan.carrier.renew_days:
                            if today+timedelta(days=i) \
                                    == autorefill.renewal_date+timedelta(days=autorefill.plan.carrier.renew_days *
                                            appeared) <= today+timedelta(days=6):
                                appeared += 1
                                if unused_pins_amount < 1:
                                    autorefill_week[i] = autorefill.calculate_cost_and_tax()[0]
                                else:
                                    unused_pins_amount -= 1
                                continue
                        if autorefill.plan.carrier.renew_months:
                            if today+timedelta(days=i) == autorefill.renewal_date:
                                if unused_pins_amount < 1:
                                    autorefill_week[i] = autorefill.calculate_cost_and_tax()[0]
                                break  # in that case it can only appear once per week
                    price = 0
                    for i in range(7):
                        if autorefill_week[i]:
                            price += autorefill_week[i]
                        result[i] += price
        return result

    def set_selling_prices(self):
        from ppars.apps.price.models import SellingPriceLevel, PlanSellingPrice
        for plan in Plan.objects.all():
            for price_level in SellingPriceLevel.objects.all():
                PlanSellingPrice.objects.create(carrier=plan.carrier,
                                                plan=plan,
                                                company=self,
                                                price_level=price_level,
                                                selling_price=plan.plan_cost)

    def set_customers_send_pin_prerefill(self, state):
        self.send_pin_prerefill = state
        for customer in Customer.objects.filter(company=self):
            customer.send_pin_prerefill = self.send_pin_prerefill
            customer.save()
        self.save()

    def set_default_notification(self):
        for customer in Customer.objects.filter(company=self):
            customer.email_success = True
            customer.precharge_sms = True
            customer.save()
        self.save()


class UserProfile(models.Model):
    """
    This model use as related model for User and CompanyProfile
    """
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigOneToOneField(User, related_name='profile', unique=False, blank=True, null=True)
    company = fields.BigForeignKey(CompanyProfile, related_name='user_profile', blank=True, null=True)
    updates_email = models.TextField(null=True, blank=True)
    superuser_profile = models.NullBooleanField()

    #date_limit_user
    license_expiries = models.BooleanField(default=False)
    date_limit_license_expiries = models.DateField(null=True, blank=True)

    def __unicode__(self):
        username = None
        if self.user:
            username = self.user.username
        return u'profile %s' % username

    def is_license_expiries(self):
        if self.license_expiries and self.date_limit_license_expiries and datetime.datetime.now(pytz.timezone('US/Eastern')).date() > self.date_limit_license_expiries:
            return False
        else:
            return True

    def get_company_users(self):
        users = []
        comps = UserProfile.objects.filter(company=self.user.profile.company)
        for comp in comps:
            users.append(comp.user)
        return users

    def get_company_logs(self):
        logs = Log.objects.filter(company=self.user.profile.company)
        for log in logs:
            log.note = log.note.split('\n')
        return logs

    def get_company_customers(self):
        customers = Customer.objects.filter(company=self.user.profile.company)
        return customers

    def get_company_autorefills(self):
        autorefills = AutoRefill.objects.filter(trigger="SC", company=self.user.profile.company)
        return autorefills


class Log(models.Model):
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    company = fields.BigForeignKey(CompanyProfile, null=True, blank=True)
    note = models.TextField(null=True)
    created = models.DateTimeField(auto_now_add=True)

    # created = models.DateTimeField()

    def get_created_est(self):
        return self.created

    class Meta:
        ordering = ["-created"]

    def __unicode__(self):
        return self.note

    def get_absolute_url(self):
        return reverse('log_list')


class Carrier(models.Model):
    SCHEDULE_TYPE_CHOICES = (
        ('MD', 'Mid-Day'),
        ('MN', '11:59 PM.'),
        ('1201AM', '12:01 AM.'),
        ('1AM', '1 AM.'),
        ('130AM', '1:30 AM.'),
        ('2AM', '2 AM.'),
    )

    id = fields.BigAutoField(primary_key=True)
    company = fields.BigForeignKey(CompanyProfile, blank=True, null=True)
    name = models.CharField(max_length=100)
    recharge_number = models.CharField(max_length=10, blank=True, null=True)
    admin_site = models.CharField(max_length=500, blank=True, null=True)
    renew_days = models.IntegerField(max_length=3, blank=True, null=True)
    renew_months = models.IntegerField(max_length=3, blank=True, null=True)
    created = models.DateTimeField("Created at", auto_now_add=True)
    updated = models.DateTimeField(verbose_name="Updated at", auto_now=True)

    default_time = models.CharField(max_length=6, choices=SCHEDULE_TYPE_CHOICES, blank=True)

    # created = models.DateTimeField("Created at")
    # updated = models.DateTimeField(verbose_name="Updated at")

    def __unicode__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('carrier_list')

    def get_created_est(self):
        return self.created

    def get_updated_est(self):
        return self.updated


class Plan(models.Model):
    DOMESTIC_PIN = 'PI'
    DOMESTIC_TOPUP = 'TP'
    PLAN_TYPE_CHOICES = (
        (DOMESTIC_TOPUP, 'Domestic Top-Up'),
        (DOMESTIC_PIN, 'Domestic Pin'),
    )
    id = fields.BigAutoField(primary_key=True)
    universal_plan = fields.BigForeignKey('self', blank=True, null=True, related_name='plan')
    universal = models.BooleanField(default=False)
    available = models.BooleanField(default=True)
    company = fields.BigForeignKey(CompanyProfile, blank=True, null=True)
    sc_sku = models.CharField(max_length=60, default='', blank=True, null=True)
    plan_id = models.CharField(max_length=30)
    # Seems like this field used for dollarphone API
    api_id = models.CharField(max_length=30, null=True, blank=True)
    plan_type = models.CharField(max_length=2, choices=PLAN_TYPE_CHOICES, default='PI')
    carrier = fields.BigForeignKey(Carrier)
    plan_name = models.CharField(max_length=100)
    plan_cost = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    created = models.DateTimeField("Created at", auto_now_add=True)
    updated = models.DateTimeField(verbose_name="Updated at", auto_now=True)

    # created = models.DateTimeField(("Created at"))
    # updated = models.DateTimeField(verbose_name=("Updated at"))

    def __unicode__(self):
        return self.plan_id

    def get_absolute_url(self):
        return reverse('plan_list')

    def get_created_est(self):
        return self.created

    def get_updated_est(self):
        return self.updated

    def get_plansellingprice(self, company, selling_price_level):
        from ppars.apps.price.models import PlanSellingPrice
        plansellingprice = PlanSellingPrice.objects.get(plan=self,
                                                        company=company,
                                                        price_level=selling_price_level)
        return plansellingprice.selling_price

    def set_selling_prices(self):
        from ppars.apps.price.models import SellingPriceLevel, PlanSellingPrice
        for company in CompanyProfile.objects.filter(superuser_profile=False):
            for price_level in SellingPriceLevel.objects.all():
                PlanSellingPrice.objects.create(carrier=self.carrier,
                                                plan=self,
                                                company=company,
                                                price_level=price_level,
                                                selling_price=self.plan_cost)


class PlanDiscount(models.Model):
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigForeignKey(User, null=True, on_delete=models.SET_NULL)
    carrier = fields.BigForeignKey(Carrier)
    plan = fields.BigForeignKey(Plan, null=True, blank=True)
    discount = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0))
    created = models.DateTimeField("Created at", auto_now_add=True)
    updated = models.DateTimeField(verbose_name="Updated at", auto_now=True)

    # created = models.DateTimeField(("Created at"))
    # updated = models.DateTimeField(verbose_name=("Updated at"))

    def __unicode__(self):
        plan = self.plan or "default"
        return "%s_%s" % (self.carrier, plan)

    def get_absolute_url(self):
        return reverse('plan_discount_list')

    def get_created_est(self):
        return self.created

    def get_updated_est(self):
        return self.updated


class Customer(models.Model):
    from ppars.apps.price.models import level_price_default
    # Must be the same as in Notification model!!!
    DONT_SEND = 'NO'
    MAIL = 'EM'
    SMS = 'SM'
    SMS_EMAIL = 'SE'
    SEND_STATUS_CHOICES = (
        (DONT_SEND, "Don't Send"),
        (SMS_EMAIL, 'SMS via Email'),
        (SMS, 'via Twilio SMS'),
        (MAIL, 'via Email'),
    )
    CASH = 'CA'
    CREDITCARD = 'CC'
    CHARGE_TYPE_CHOICES = (
        (CASH, 'Cash'),
        (CREDITCARD, 'Credit Card'),
    )
    DOLLARPHONE = 'DP'
    AUTHORIZE = 'A'
    USAEPAY = 'U'
    CASH_PREPAYMENT = 'CP'
    CHARGE_GETAWAY_CHOICES = (
        (AUTHORIZE, 'Authorize'),
        (USAEPAY, 'USAePay'),
        (DOLLARPHONE, 'DollarPhone'),
        (CASH, 'Cash'),
        (CASH_PREPAYMENT, 'Cash(PrePayment)'),
    )
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigForeignKey(User, null=True, on_delete=models.SET_NULL)
    company = fields.BigForeignKey(CompanyProfile, blank=True, null=True)

    first_name = models.CharField(max_length=30)
    middle_name = models.CharField(max_length=30, blank=True, null=True)
    last_name = models.CharField(max_length=30)

    primary_email = models.EmailField(blank=True)
    primary_email_lowercase = models.EmailField(null=True, blank=True)

    sc_account = models.CharField(max_length=30, blank=True, null=True)

    address = models.CharField(max_length=500, blank=True, null=True)
    city = models.CharField(max_length=30, blank=True, null=True)
    state = models.CharField(max_length=30, blank=True, null=True)
    zip = models.CharField(max_length=10, blank=True)

    charge_type = models.CharField(max_length=2, choices=CHARGE_TYPE_CHOICES, default='CA')
    creditcard = models.CharField(max_length=20, blank=True, null=True)
    charge_getaway = models.CharField(max_length=3, choices=CHARGE_GETAWAY_CHOICES, blank=True)

    save_to_authorize = models.BooleanField(default=False)
    save_to_usaepay = models.BooleanField(default=False)
    save_to_local = models.BooleanField(default=False)

    authorize_id = models.CharField(max_length=30, blank=True, null=True)
    usaepay_customer_id = models.CharField(max_length=30, blank=True, null=True)
    usaepay_custid = models.CharField(max_length=100, blank=True)

    selling_price_level = models.ForeignKey('price.SellingPriceLevel', default=level_price_default)
    customer_discount = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0), blank=True)
    taxable = models.BooleanField(default=True)

    send_status = models.CharField(max_length=2, choices=SEND_STATUS_CHOICES, default=DONT_SEND)
    email_success = models.BooleanField(default=True)
    group_sms = models.BooleanField(default=True)
    precharge_sms = models.BooleanField(default=True)
    enabled = models.BooleanField(default=True)

    send_pin_prerefill = models.CharField(max_length=2, choices=SEND_STATUS_CHOICES, default=DONT_SEND)

    created = models.DateTimeField("Created at", auto_now_add=True)
    updated = models.DateTimeField(verbose_name="Updated at", auto_now=True)
    sms_email = models.CommaSeparatedIntegerField(max_length=500, blank=True)
    sms_gateway = models.ForeignKey('notification.SmsEmailGateway', default=1)
    notes = models.TextField(null=True, blank=True)
    # # for import
    # created = models.DateTimeField("Created at" )
    # updated = models.DateTimeField(verbose_name="Updated at")

    def __unicode__(self):
        return " ".join([self.first_name, self.middle_name or '', self.last_name])


    @property
    def full_name(self):
        return " ".join([self.first_name, self.middle_name or '', self.last_name])

    def phone_numbers_list(self):
        numbers = PhoneNumber.objects.filter(customer=self)
        if numbers:
            result = list()
            for number in numbers:
                result.append('%s:%s:%s' %
                              (number,
                               url_with_querystring(reverse('manualrefill'), ph=number, cid=self.id),
                               url_with_querystring(reverse('autorefill_create'), ph=number, cid=self.id)))
            return result

    def get_absolute_url(self):
        return reverse('customer_list')

    def get_created_est(self):
        return self.created

    def get_updated_est(self):
        return self.updated

    def set_primary_email_to_lowercase(self):
        if self.primary_email:
            self.primary_email_lowercase = self.primary_email.lower()

    def set_phone_numbers(self, phone_numbers):
        if phone_numbers:
            for pn in PhoneNumber.objects.filter(customer=self):
                if pn.number not in phone_numbers:
                    pn.customer = None
                    pn.save()
                    for ar in AutoRefill.objects.filter(phone_number=pn.number):
                        ar.enabled = False
                        ar.save()
            for number in phone_numbers:
                PhoneNumber.objects.get_or_create(number=number,
                                                  company=get_request().user.profile.company,
                                                  customer=self)
        self.set_sms_email_to_first_phone_number()

    def create_card_to_usaepay(self, data):
        token = self.company.usaepay_authorization()
        client = SoapClient(wsdl=settings.USAEPAY_WSDL,
                            trace=False,
                            ns=False)
        response = client.addCustomer(Token=token, CustomerData=data)
        return response['addCustomerReturn']

    def update_card_to_usaepay(self, data):
        token = self.company.usaepay_authorization()
        result = False
        if token:
            client = SoapClient(wsdl=settings.USAEPAY_WSDL,
                                trace=True,
                                ns=False)
            response = client.quickUpdateCustomer(CustNum=self.usaepay_customer_id,
                                                  Token=token,
                                                  UpdateData=data)
            result = response
        return result



    @property
    def has_local_cards(self):
        # from ppars.apps.card.models import Card
        return bool(self.cards.exists())
        # return True

    def get_local_card(self):
        if self.has_local_cards:
            return self.cards.all()[0]
        return None

    @property
    def get_local_card_expiration_month(self):
        if self.has_local_cards:
            card = self.cards.all()[0]
            return card.expiration_month
        return None

    @property
    def get_local_card_expiration_year(self):
        if self.has_local_cards:
            card = self.cards.all()[0]
            return card.expiration_year
        return None

    def save_local_card(self, number, cvv, year, month):
        from ppars.apps.card.models import Card
        obj = self.get_local_card()
        if not obj:
            obj = Card()
            # obj.customer = self
        if number:
            obj.number = number
        if cvv:
            obj.cvv = cvv
        if year:
            obj.expiration_year = year
        if month:
            obj.expiration_month = month
        obj.save()
        return obj

    def set_sms_email_to_first_phone_number(self):
        if not self.sms_email:
            phone = PhoneNumber.objects.filter(customer=self).first()
            if not phone is None and phone != '':
                self.sms_email = phone.number
                self.save()

    @property
    def get_charge_getaway(self):
        if self.charge_getaway:
            getaway = self.charge_getaway
        else:
            getaway = self.company.cccharge_type
        return getaway


class PhoneNumber(models.Model):
    company = fields.BigForeignKey(CompanyProfile)
    customer = fields.BigForeignKey(Customer, null=True)
    number = models.CharField(max_length=12)

    def __unicode__(self):
        return u'%s' % self.number


class CarrierAdmin(models.Model):
    id = fields.BigAutoField(primary_key=True)
    company = fields.BigForeignKey(CompanyProfile, blank=True, null=True)
    carrier = fields.BigForeignKey(Carrier)
    username = models.CharField(max_length=100)
    password = models.CharField(max_length=100)
    created = models.DateTimeField(("Created at"), auto_now_add=True)
    updated = models.DateTimeField(verbose_name=("Updated at"), auto_now=True)
    # created = models.DateTimeField(("Created at"))
    # updated = models.DateTimeField(verbose_name=("Updated at"))

    def __unicode__(self):
        return u'%s' % self.carrier

    def get_absolute_url(self):
        return reverse('carrier_admin_list')

    def get_created_est(self):
        return self.created

    def get_updated_est(self):
        return self.updated


class AutoRefill(models.Model):
    MD = 'MD'
    MN = 'MN'
    AFTER_MID_NIGHT = '1201AM'
    ONE_AM = '1AM'
    ONE_HALF_HOUR_AM = '130AM'
    TWO_AM = '2AM'
    AM_AND_ONE_MINUET_PM = '12pm&1201am'

    SCHEDULE_TYPE_CHOICES = (
        (MD, 'Mid-Day'),
        (MN, '11:59 PM.'),
        (AFTER_MID_NIGHT, '12:01 AM.'),
        (ONE_AM, '1 AM.'),
        (ONE_HALF_HOUR_AM, '1:30 AM.'),
        (TWO_AM, '2 AM.'),
        (AM_AND_ONE_MINUET_PM, '12PM & 12:01AM'),
    )
    REFILL_FR = 'FR'
    REFILL_GP = 'GP'
    REFILL_TYPE_CHOICES = (
        (REFILL_FR, 'Full Refill/Topup'),
        (REFILL_GP, 'Get Pin'),
    )
    TRIGGER_MN = 'MN'
    TRIGGER_SC = 'SC'
    TRIGGER_AP = 'AP'
    TRIGGER_TYPE_CHOICES = (
        (TRIGGER_MN, 'Manual Refill'),
        (TRIGGER_SC, 'Scheduled Refill'),
        (TRIGGER_AP, 'API Refill'),
    )
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigForeignKey(User, null=True, on_delete=models.SET_NULL)
    company = fields.BigForeignKey(CompanyProfile, blank=True, null=True)
    customer = fields.BigForeignKey(Customer)
    phone_number = models.CharField(max_length=12)
    plan = fields.BigForeignKey(Plan)
    refill_type = models.CharField(max_length=2, default=REFILL_FR, choices=REFILL_TYPE_CHOICES)
    renewal_date = models.DateField(blank=True, null=True)
    renewal_end_date = models.DateField(blank=True, null=True)
    renewal_interval = models.IntegerField(max_length=3, blank=True, null=True)
    last_renewal_status = models.CharField(max_length=50, null=True, blank=True)
    last_renewal_date = models.DateField(blank=True, null=True)
    schedule = models.CharField(max_length=11, choices=SCHEDULE_TYPE_CHOICES, default=MN, null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True, null=True)
    trigger = models.CharField(max_length=2, choices=TRIGGER_TYPE_CHOICES, blank=True)
    pin = models.CharField(max_length=256, null=True, blank=True)
    enabled = models.BooleanField(default=True)
    created = models.DateTimeField(("Created at"), auto_now_add=True)
    updated = models.DateTimeField(verbose_name=("Updated at"), auto_now=True)
    pre_refill_sms = models.BooleanField(default=False)
    pre_refill_sms_number = models.CharField(max_length=12, null=True, blank=True)

    # created = models.DateTimeField(("Created at"))
    # updated = models.DateTimeField(verbose_name=("Updated at"))

    def set_prerefill_phone_number_to_phone_number(self):
        if not self.pre_refill_sms_number:
            self.pre_refill_sms_number = self.phone_number

    def __unicode__(self):
        return u"%s" % self.id

    def get_absolute_url(self):
        return reverse('autorefill_list')

    def get_created_est(self):
        return self.created

    def get_updated_est(self):
        return self.updated

    def check_twilio_confirm_sms(self):
        '''
        :param autorefill:
        :return: check if customer confirmed scheduled transaction
        '''
        if self.company.twilio_sid and \
                self.company.twilio_auth_token and \
                self.company.twilio_number:
            today = datetime.datetime.now(timezone('US/Eastern')).date()
            client = TwilioRestClient(self.company.twilio_sid, self.company.twilio_auth_token)
            list_of_messages = (client.messages.list(to="+1%s" % self.company.twilio_number, from_=self.phone_number, date_send=today)
                                + client.messages.list(to="+1%s" % self.company.twilio_number, from_=self.phone_number, date_send=today-timedelta(days=6))
                                + client.messages.list(to="+1%s" % self.company.twilio_number, from_=self.phone_number, date_send=today-timedelta(days=5))
                                + client.messages.list(to="+1%s" % self.company.twilio_number, from_=self.phone_number, date_send=today-timedelta(days=4))
                                + client.messages.list(to="+1%s" % self.company.twilio_number, from_=self.phone_number, date_send=today-timedelta(days=3))
                                + client.messages.list(to="+1%s" % self.company.twilio_number, from_=self.phone_number, date_send=today-timedelta(days=2))
                                + client.messages.list(to="+1%s" % self.company.twilio_number, from_=self.phone_number, date_send=today-timedelta(days=1)))  # list of messages for last six days
            for sms in list_of_messages:  # if there were any messages with text 'yes' return True
                if sms.body.upper() == 'yes'.upper():
                    return True
        return False

    def create_charge(self, amount, tax):
        from ppars.apps.charge.models import Charge
        if self.customer.charge_getaway:
            getaway = self.customer.charge_getaway
        else:
            getaway = self.company.cccharge_type
        cc_charge = Charge.objects.create(
            company=self.company,
            autorefill=self,
            customer=self.customer,
            creditcard=self.customer.creditcard,
            payment_getaway=getaway,
            amount=amount,
            tax=tax
        )
        return cc_charge

    def check_renewal_end_date(self, today=None, commit=True):
        if not today:
            today = datetime.datetime.now(timezone('US/Eastern')).date()
        if self.renewal_end_date and self.renewal_end_date < today:
            self.enabled = False
            if commit:
                self.save()
        return self.enabled

    def set_renewal_date_to_next(self, today=None, commit=True):
        if not today:
            today = datetime.datetime.now(timezone('US/Eastern')).date()

        logger.debug('set_renewal_date_to_next 1 %s', (self, self.last_renewal_date, today))
        self.last_renewal_date = today
        if self.plan.carrier.renew_days:
            logger.debug('set_renewal_date_to_next 2 %s', (self, self.last_renewal_date, today + timedelta(days=self.plan.carrier.renew_days)))
            self.renewal_date = today + timedelta(days=self.plan.carrier.renew_days)
        elif self.plan.carrier.renew_months:
            logger.debug('set_renewal_date_to_next 3 %s', (self, self.last_renewal_date, today + relativedelta(months=self.plan.carrier.renew_months)))
            self.renewal_date = today + relativedelta(months=self.plan.carrier.renew_months)
        if self.renewal_interval:
            logger.debug('set_renewal_date_to_next 4 %s', (self, self.last_renewal_date, today + timedelta(days=self.renewal_interval)))
            self.renewal_date = today + relativedelta(days=self.renewal_interval)
        if commit:
            self.save()

    def calculate_cost_and_tax(self):
        selling_price = self.plan.get_plansellingprice(self.company, self.customer.selling_price_level)
        cost = selling_price - self.customer.customer_discount
        tax = decimal.Decimal(0.0)
        if self.customer.taxable:
            tax = self.company.tax
            cost = cost + cost * tax/decimal.Decimal(100)
        cost = cost.quantize(decimal.Decimal('.01'), rounding=decimal.ROUND_HALF_UP)
        return cost, tax


class Transaction(models.Model):
    QUEUED = 'Q'
    PROCESS = 'P'
    RETRY = 'R'
    COMPLETED = 'C'
    INTERMEDIATE = 'I'
    SUCCESS = 'S'
    WAITING = 'W'
    ERROR = 'E'
    STATE_TYPE_CHOICES = (
        (QUEUED, 'Queued'),
        (PROCESS, 'In Process'),
        (RETRY, 'Retry'),
        (COMPLETED, 'Completed'),
        (INTERMEDIATE, 'Intermediate'),
    )
    STATUS_TYPE_CHOICES = (
        (SUCCESS, 'Success'),
        (WAITING, 'Waiting'),
        (ERROR, 'Error'),
    )
    TRIGGER_TYPE_CHOICES = (
        ('MN', 'Manual Refill'),
        ('SC', 'Scheduled Refill'),
        ('AP', 'API Refill'),
    )
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigForeignKey(User, null=True, on_delete=models.SET_NULL)
    company = fields.BigForeignKey(CompanyProfile, blank=True, null=True)
    plan_str = models.CharField(max_length=256, null=True)
    phone_number_str = models.CharField(max_length=12, null=True)
    customer_str = models.CharField(max_length=256, null=True)
    refill_type_str = models.CharField(max_length=256, null=True)
    autorefill = fields.BigForeignKey(AutoRefill, on_delete=models.SET_NULL, null=True)
    locked = models.BooleanField(default=False)
    paid = models.BooleanField(default=False)
    completed = models.BooleanField(default=False)
    pin_error = models.BooleanField(default=False)
    state = models.CharField(max_length=3, choices=STATE_TYPE_CHOICES, blank=True)
    status = models.CharField(max_length=3, choices=STATUS_TYPE_CHOICES, null=True, blank=True)
    current_step = models.CharField(max_length=30, null=True, blank=True)
    adv_status = models.CharField(max_length=500, null=True, blank=True)
    cost = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0))
    need_paid = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0))
    create_asana_ticket = models.BooleanField(default=False)
    profit = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0))
    pin = models.CharField(max_length=256, null=True, blank=True)
    sellercloud_order_id = models.CharField(max_length=200, blank=True, null=True)
    sellercloud_note_id = models.CharField(max_length=200, blank=True, null=True)
    sellercloud_payment_id = models.CharField(max_length=200, blank=True, null=True)
    retry_count = models.IntegerField(null=True, blank=True)
    customer_confirmation = models.BooleanField(default=False)
    # We stored date in GMT but needs to use in US/Eastern
    started = models.DateTimeField("Started at", auto_now_add=True)
    # todo ftf? need to set default=None
    ended = models.DateTimeField(verbose_name="Ended at", auto_now=True)
    triggered_by = models.CharField(editable=False, max_length=32, default='System')
    trigger = models.CharField(max_length=50, editable=False, choices=TRIGGER_TYPE_CHOICES, blank=True, default='')

    # started = models.DateTimeField(("Started at"))
    # ended = models.DateTimeField(verbose_name=("Ended at"))

    def __unicode__(self):
        return u'%s' % self.id

    @property
    def customer(self):
        return self.autorefill.customer

    def save(self, *args, **kwargs):
        self.plan_str = self.autorefill.plan.plan_id
        self.phone_number_str = self.autorefill.phone_number
        self.customer_str = " ".join([self.autorefill.customer.first_name, self.autorefill.customer.middle_name or '', self.autorefill.customer.last_name])
        self.refill_type_str = self.autorefill.get_refill_type_display()
        # if self.cost == u'':
        #     self.cost = 0.0
        # if self.profit == u'':
        #     self.profit = 0.0
        super(Transaction, self).save(*args, **kwargs)

    def get_started_est(self):
        return self.started

    def get_ended_est(self):
        return self.ended

    def get_full_url(self):
        return '%s%s' % (settings.SITE_DOMAIN, reverse('transaction_detail', args=[self.pk]))

    def get_pin_url(self):
        if self.pin:
            data_pins = []
            pins = UnusedPin.objects.filter(pin=self.pin, company=self.company)
            if pins:
                for pin in pins:
                    data_pins.append('<a href="%s">%s</a> ' % (reverse('unusedpin_update', args=[pin.id]), self.pin))
                    return '<br/>'.join(data_pins)
            else:
                return '%s' % self.pin
        return ''

    def charge_list(self):
        from ppars.apps.charge.models import TransactionCharge
        return TransactionCharge.objects.filter(transaction=self)

    def add_transaction_step(self, current_step, action, status, adv_status):
        TransactionStep.objects.create(
            operation=current_step,
            transaction=self,
            action=action,
            status=status,
            adv_status=adv_status
        )

    def cost_calculation(self):
        cost, tax = self.autorefill.calculate_cost_and_tax()
        self.cost = cost
        discount = 0
        if PlanDiscount.objects.filter(carrier=self.autorefill.plan.carrier,
                                       plan=self.autorefill.plan).exists():
            discount = PlanDiscount.objects.get(carrier=self.autorefill.plan.carrier,
                                                plan=self.autorefill.plan).discount
        elif PlanDiscount.objects.filter(carrier=self.autorefill.plan.carrier, plan=None).exists():
            discount = PlanDiscount.objects.get(carrier=self.autorefill.plan.carrier, plan=None).discount
        self.profit = self.cost * (discount/100)
        self.save()
        return cost, tax

    def check_sms_confirmation(self):
        if self.autorefill.check_twilio_confirm_sms():
            TransactionStep.objects.create(operation='Checking for sms confirmation',
                                           transaction=self,
                                           action='Check sms confirmation',
                                           status='S',
                                           adv_status='Transaction confirmed via SMS')
            return True
        else:
            TransactionStep.objects.create(operation='Checking for sms confirmation',
                                           transaction=self,
                                           action='Check sms confirmation',
                                           status='S',
                                           adv_status='Canceled. Transaction wasn`t confirmed via SMS')
            return False

    def send_payment_to_sellercloud_order(self):
        if settings.TEST_MODE:
            TransactionStep.objects.create(operation='Create send_payment_to_sellercloud_order in TEST MODE',
                                           transaction=self,
                                           action='Begin',
                                           status='S',
                                           adv_status='Begin send_payment_to_sellercloud_order in TEST MODE')
            return
        if self.sellercloud_payment_id:
            TransactionStep.objects.create(operation='send payments to SellerCloud',
                                           transaction=self,
                                           action='Check',
                                           status='S',
                                           adv_status='Payments for order %s already exist' % (self.sellercloud_order_id))
            return
        if self.paid:
            client = SoapClient(wsdl="http://kc.ws.sellercloud.com/OrderCreationService.asmx?WSDL", trace=True,
                                ns=False)
            xmlns="http://api.sellercloud.com/"
            header = SimpleXMLElement('<Headers/>', )
            security = header.add_child("AuthHeader")
            security['xmlns'] = xmlns
            security.marshall('UserName', self.user.get_company_profile().sc_email)
            security.marshall('Password', self.user.get_company_profile().sc_password)
            client['AuthHeader'] = security
            if self.customer.charge_type == 'CC':
                payment_method = 'CreditCard'
            else:
                payment_method = 'Cash'
            response = client.Orders_AddPaymentToOrder(
                OrderID=u'%s' % self.sellercloud_order_id,
                amount=self.cost,
                paymentMethod=payment_method,
            )
            result = response['Orders_AddPaymentToOrderResult']
            TransactionStep.objects.create(operation='send payments to SellerCloud',
                                           transaction=self,
                                           action='send payments',
                                           status='S',
                                           adv_status='Order payments <a href="http://kc.cwa.sellercloud.com/'
                                                      'Orders/Orders_Details.aspx?Id=%s" target="_blank" >'
                                                      '%s</a> created successfully' %
                                                      (self.sellercloud_order_id, self.sellercloud_order_id),)
            self.sellercloud_payment_id = result
            self.save()

    def send_note_to_sellercloud_order(self):
        if settings.TEST_MODE:
            TransactionStep.objects.create(operation='Create send_note_to_sellercloud_order in TEST MODE',
                                           transaction=self,
                                           action='Begin',
                                           status='S',
                                           adv_status='Begin send_note_to_sellercloud_order in TEST MODE')
            return
        if self.sellercloud_note_id:
            return
        client = SoapClient(wsdl="http://kc.ws.sellercloud.com/OrderCreationService.asmx?WSDL", trace=True,
                            ns=False)
        xmlns = "http://api.sellercloud.com/"
        header = SimpleXMLElement('<Headers/>', )
        security = header.add_child("AuthHeader")
        security['xmlns'] = xmlns
        security.marshall('UserName', self.user.get_company_profile().sc_email)
        security.marshall('Password', self.user.get_company_profile().sc_password)
        client['AuthHeader'] = security
        note = u'transaction:  %s  transaction status: %s' % (self.get_full_url(), self.adv_status)
        order = u'%s' % self.sellercloud_order_id
        response = client.CreateOrderNote(OrderId=order, note=note)
        self.sellercloud_note_id = response['CreateOrderNoteResult']
        self.save()

    def send_tratsaction_to_sellercloud(self):
        if settings.TEST_MODE:
            TransactionStep.objects.create(operation='Create SellerCloud order in TEST MODE',
                                           transaction=self,
                                           action='Begin',
                                           status='S',
                                           adv_status='Begin create SellerCloud order in TEST MODE')
            return
        if not (self.company.sc_company_id and self.company.sc_email
                and self.company.sc_password):
            TransactionStep.objects.create(operation='send_to_sc',
                                           transaction=self,
                                           action='get authorization token',
                                           status='E',
                                           adv_status='Please check SellerCloud authorization tokens')
            return
        if self.sellercloud_order_id:
            TransactionStep.objects.create(operation='Create SellerCloud order',
                                           transaction=self,
                                           action='Check',
                                           status='S',
                                           adv_status='SellerCloud order already exist')
            return
        TransactionStep.objects.create(operation='Create SellerCloud order',
                                       transaction=self,
                                       action='Begin',
                                       status='S',
                                       adv_status='Begin create SellerCloud order')
        client = SoapClient(wsdl="http://kc.ws.sellercloud.com/OrderCreationService.asmx?WSDL", trace=False,
                            ns=False)
        xmlns = "http://api.sellercloud.com/"
        header = SimpleXMLElement('<Headers/>', )
        security = header.add_child("AuthHeader")
        security['xmlns'] = xmlns
        security.marshall('UserName', self.user.get_company_profile().sc_email)
        security.marshall('Password', self.user.get_company_profile().sc_password)
        client['AuthHeader'] = security
        new_order = {
            u'LockShippingMethod': True,
            u'OrderCreationSourceApplication': 'Default',
            # u'Customer_TaxID': <type 'str'>,
            # u'Customer_TaxExempt': <type 'bool'>,
            # u'CouponCode': <type 'str'>,
            # u'TaxRate': <class 'decimal.Decimal'>,
            # u'ParentOrderID': <type 'int'>,
            # u'ShipFromWarehouseId': <type 'int'>,
            # u'SalesRepId': <type 'int'>,
            u'DiscountTotal':  self.profit,
            # u'RushOrder': <type 'bool'>,
            # u'Payments': [
            #         {u'OrderPaymentDetails':
            #            {
            #             # u'StoreCouponOrGiftCertificateID': <type 'int'>,
            #             u'Amount':  self.cost,
            #             u'PaymentMethod': payment_method,
            #             u'CreditCardType': None,
            #             # u'CreditCardNumber': <type 'str'>,
            #             # u'CreditCardSecurityCode': <type 'str'>,
            #             # u'CreditCardCVV2Response': <type 'str'>,
            #             # u'CreditCardCardExpirationMonth': <type 'int'>,
            #             # u'CreditCardCardExpirationYear': <type 'int'>,
            #             u'PaymentFirstName': self.customer.first_name,
            #             u'PaymentLastName': self.customer.last_name,
            #             u'PaymentTransactionID': self.id,
            #             u'PaymentStatus': 'Cleared',
            #             u'PaymentClearanceDate': self.ended,
            #             u'PaymentEmailAddress': self.customer.primary_email
            # }}
            # ],
            u'Items': [
                {u'OrderItemDetails': {
                    # u'ShipType': <type 'str'>,
                    # u'ReturnReason': <type 'str'>,
                    # u'ShipFromWarehouseID': <type 'int'>,
                    # u'ExportedProductID': <type 'str'>,
                    # u'VariantID': <type 'long'>,
                    # u'SalesOutlet': <type 'str'>,
                    u'SerialNumbers': [{u'string': u'%s' % self.pin}],
                    # u'DiscountTotal': <class 'decimal.Decimal'>,
                    u'DiscountAmount':  self.profit,
                    # u'DiscountType': <type 'str'>,
                    # u'QtyReturned': <type 'int'>,
                    # u'QtyShipped': <type 'int'>,
                    # u'OrderItemUniqueIDInDB': <type 'int'>,
                    u'SKU': self.autorefill.plan.sc_sku,
                    u'ItemName': self.autorefill.plan.plan_name,
                    u'Qty': 1,
                    # u'OrderSourceItemID': <type 'str'>,
                    # u'OrderSourceTransactionID': <type 'str'>,
                    u'UnitPrice': self.cost,
                    u'ShippingPrice': 0.0,
                    u'SubTotal':  self.cost,
                    u'Notes': u''
                }}
            ],
            # u'Packages': [
            #    {u'OrderPackageDetails': {
            #        u'Carrier': <type 'str'>,
            #        u'ShipMethod': <type 'str'>,
            #        u'TrackingNumber': <type 'str'>,
            #        u'ShipDate': <type 'datetime.datetime'>,
            #        u'FinalShippingCost': <class 'decimal.Decimal'>,
            #        u'ShippingWeight': <class 'decimal.Decimal'>,
            #        u'ShippingWidth': <class 'decimal.Decimal'>,
            #        u'ShippingLength': <class 'decimal.Decimal'>,
            #        u'ShippingHeight': <class 'decimal.Decimal'>
            #    }}
            # ],
            u'CompanyID': self.user.get_company_profile().sc_company_id,
            u'OrderSource': 'Local_Store',
            u'OrderSourceOrderID': self.id,
            u'OrderDate': datetime.datetime.now(timezone('US/Eastern')),
            u'CustomerFirstName': self.customer.first_name,
            u'CustomerLastName': self.customer.last_name,
            u'CustomerEmail': self.customer.primary_email or '%s@%s' % (self.customer.sms_email, self.customer.sms_gateway.gateway),
            u'BillingAddressFirstName': self.customer.first_name,
            u'BillingAddressLastName': self.customer.last_name,
            # u'BillingAddressCompany': <type 'str'>,
            u'BillingAddressStreet1': self.customer.address,
            # u'BillingAddressStreet2': <type 'str'>,
            u'BillingAddressCity': self.customer.city,
            u'BillingAddressState': self.customer.state,
            u'BillingAddressZipCode': self.customer.zip,
            u'BillingAddressCountry': "United States",
            # u'BillingAddressPhone': <type 'str'>,
            u'ShippingAddressFirstName': self.customer.first_name,
            u'ShippingAddressLastName': self.customer.last_name,
            # u'ShippingAddressCompany': <type 'str'>,
            u'ShippingAddressStreet1': self.customer.address,
            # u'ShippingAddressStreet2': <type 'str'>,
            u'ShippingAddressCity': self.customer.city,
            u'ShippingAddressState': self.customer.state,
            u'ShippingAddressZipCode': self.customer.zip,
            u'ShippingAddressCountry': "United States",
            u'ShippingAddressPhone': u'%s' % self.phone_number_str,
            # u'ShippingMethod': <type 'str'>,
            # u'ShippingCarrier': <type 'str'>,
            # u'CustomerComments': <type 'str'>,
            u'ShippingStatus': 'FullyShipped',
            u'PaymentStatus': 'NoPayment',
            u'SubTotal':  self.cost,
            # u'TaxTotal': <class 'decimal.Decimal'>,
            # u'ShippingTotal': <class 'decimal.Decimal'>,
            # u'GiftWrapTotal': <class 'decimal.Decimal'>,
            # u'AdjustmentTotal': <class 'decimal.Decimal'>,
            u'GrandTotal':  self.cost,
            }
        response = client.CreateNewOrder(order=new_order)
        result = response['CreateNewOrderResult']
        TransactionStep.objects.create(operation='send transaction to SellerCloud',
                                       transaction=self,
                                       action='create order',
                                       status='S',
                                       adv_status='SC order <a href="http://kc.cwa.sellercloud.com/Orders/Orders_Details.aspx?Id=%s"'
                                                  ' target="_blank" >%s</a> created successfully' % (result, result),)
        self.sellercloud_order_id = result
        self.save()

    def send_asana(self):
        if settings.TEST_MODE:
            self.add_transaction_step('notification',
                                      'Asana',
                                      TransactionStep.SUCCESS,
                                      'TEST MODE')
            return

        if not (self.company.asana_api_key and self.company.asana_workspace
                and self.company.asana_project_name
                and self.company.asana_user):
            self.add_transaction_step('notification',
                                      'Asana',
                                      TransactionStep.ERROR,
                                      'Please check asana authorization tokens')
            return

        if self.paid:
            self.add_transaction_step('notification',
                                      'Asana',
                                      TransactionStep.SUCCESS,
                                      'Refill was paid. Order wasn`t created')
            return

        if self.create_asana_ticket:
            self.add_transaction_step('notification',
                                      'Asana',
                                      TransactionStep.SUCCESS,
                                      'Order already exists')
            return

        project_name = self.company.asana_project_name
        workspace = int(self.company.asana_workspace)
        user = self.company.asana_user
        tag_name = ''
        title = '%s-%s' % (self.customer.sc_account, self.customer_str)
        note = '%s \nphone number %s\nrefill type: %s\ncost %s$\ncharge type: %s \n' \
               'http://kc.cwa.sellercloud.com/Orders/Orders_Details.aspx?Id=%s\n' \
               'http://kc.cwa.sellercloud.com/Users/User_Orders.aspx?ID=%s' \
               % (self.customer_str, self.phone_number_str, self.autorefill.get_refill_type_display(),
                  self.cost, self.customer.get_charge_type_display(), self.customer.sc_account,
                  self.customer.sc_account)

        asana_api = asana.AsanaAPI(self.company.asana_api_key, debug=True)
        projects = asana_api.list_projects(workspace=workspace, include_archived=False)
        # print 'projects %s' %projects

        project_id = None
        for pr in projects:
            # print 'project %s' % pr
            if project_name == pr['name']:
                project_id = pr['id']
                break
        else:
            project = asana_api.create_project(project_name, workspace)
            project_id = project['id']
        # print 'project_id %s' % project_id

        data = []
        data.append(project_id)
        # print 'dictionary %s' %data

        tasks = asana_api.get_project_tasks(project_id, include_archived=False)
        # print 'tasks %s' %tasks

        task_id = None
        for ts in tasks:
            # print 'task %s' % ts
            if title == ts['name']:
                task_id = ts['id']
                task = asana_api.get_task(task_id)
                if not task['completed']:
                    notes = '%s \n%s' % (task['notes'], note)
                    asana_api.update_task(task_id, notes=notes, assignee=user)
                    break
        else:
            task = asana_api.create_task(
                title,
                workspace,
                assignee=user,
                assignee_status='later',
                # completed=False,
                # due_on=None,
                # followers=None,
                notes=note,
                projects=data,
            )
            task_id = task['id']
        # print 'task_id %s' %task_id

        # tags = asana_api.get_tags(workspace)
        # tag_id = None
        # for tg in tags:
        #     # print 'tag %s' % tg
        #     if tag_name == tg['name']:
        #         tag_id = tg['id']
        #         break
        # else:
        #     try:
        #         tag = asana_api.create_tag(tag_name, workspace)
        #         tag_id = tag['id']
        #     except asana.AsanaException, msg:
        #         print msg
        #
        # # print 'tag_id %s' %tag_id
        # try:
        #     asana_api.add_tag_task(task_id, tag_id)
        # except asana.AsanaException, msg:
        #         print msg
        self.create_asana_ticket = True
        self.save()
        self.add_transaction_step('notification',
                                  'Asana',
                                  TransactionStep.SUCCESS,
                                  'Asana order created successfully')

    def log_error_in_asana(self, error):
        if settings.TEST_MODE:
            return

        try:
            admin_company = CompanyProfile.objects.get(superuser_profile=True)
            if not admin_company.use_asana:
                return
            project_name = admin_company.asana_project_name
            workspace = int(admin_company.asana_workspace)
            current_time = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%m/%d/%y %H:%M:%S")
            title = 'Transaction %s' % self
            note = '[%s] step: %s\n %s' % (current_time, self.current_step, error)
            asana_api = asana.AsanaAPI(admin_company.asana_api_key, debug=False)
            #[{u'id': 16428561128039, u'name': u'PY3PI INC'}, {u'id': 6456132996391, u'name': u'e-zoffer.com'}, {u'id': 498346170860, u'name': u'Personal Projects'}]
            projects = asana_api.list_projects(workspace=workspace, include_archived=False)
            for pr in projects:
                # print 'project %s' % pr
                if project_name == pr['name']:
                    project_id = pr['id']
                    break
            else:
                project = asana_api.create_project(project_name, workspace)
                project_id = project['id']
            data = []
            data.append(project_id)
            tasks = asana_api.get_project_tasks(project_id, include_archived=False)
            for ts in tasks:
                # print 'task %s' % ts
                if title == ts['name']:
                    task_id = ts['id']
                    task = asana_api.get_task(task_id)
                    if not task['completed']:
                        notes = '%s \n%s' % (task['notes'], note)
                        asana_api.update_task(task_id, notes=notes)
                        break
            else:
                asana_api.create_task(
                    title,
                    workspace,
                    assignee_status='later',
                    followers=[12955112053049],
                    notes=u'%s\n%s' % (self.get_full_url(), note),
                    projects=data,
                )
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))


class TransactionStep(models.Model):
    SUCCESS = 'S'
    WAITING = 'W'
    ERROR = 'E'
    STATUS_TYPE_CHOICES = (
        (SUCCESS, 'Success'),
        (WAITING, 'Waiting'),
        (ERROR, 'Error'),
    )
    id = fields.BigAutoField(primary_key=True)
    transaction = fields.BigForeignKey(Transaction)
    operation = models.CharField(max_length=200)
    action = models.CharField(max_length=200)
    status = models.CharField(max_length=3, choices=STATUS_TYPE_CHOICES)
    adv_status = models.CharField(max_length=500, null=True)
    created = models.DateTimeField("Timestamp", auto_now=True)

    # created = models.DateTimeField(("Timestamp"))

    def __unicode__(self):
        return self.operation

    def get_created_est(self):
        return self.created


class TransactionError(models.Model):
    transaction = fields.BigForeignKey(Transaction)
    step = models.CharField(max_length=50)
    message = models.CharField(max_length=500)
    created = models.DateTimeField(auto_now=True)

    def __unicode__(self):
        return u'%s [%s]' % (self.step, self.created)


class UnusedPin(models.Model):
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigForeignKey(User, null=True, on_delete=models.SET_NULL)
    company = fields.BigForeignKey(CompanyProfile, blank=True, null=True)
    plan = fields.BigForeignKey(Plan)
    transaction = fields.BigForeignKey(Transaction, null=True, blank=True)
    pin = models.CharField(max_length=256)
    used = models.BooleanField()
    notes = models.CharField(max_length=500, blank=True)
    created = models.DateTimeField("Started at", auto_now_add=True)
    updated = models.DateTimeField(verbose_name="Ended at", auto_now=True)

    # created = models.DateTimeField(("Started at"))
    # updated = models.DateTimeField(verbose_name=("Ended at"))

    def __unicode__(self):
        return self.pin

    def get_absolute_url(self):
        return reverse('unusedpin_list')

    def get_created_est(self):
        return self.created

    def get_updated_est(self):
        return self.updated


class CaptchaLogs(models.Model):
    id = fields.BigAutoField(primary_key=True)
    user = fields.BigForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    user_name = models.CharField(max_length=200, blank=True, null=True)
    customer = fields.BigForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL)
    customer_name = models.CharField(max_length=200, blank=True, null=True)
    carrier = fields.BigForeignKey(Carrier, null=True, blank=True)
    carrier_name = models.CharField(max_length=200, blank=True, null=True)
    plan = fields.BigForeignKey(Plan, null=True, blank=True)
    plan_name = models.CharField(max_length=200, blank=True, null=True)
    refill_type = models.CharField(max_length=200, blank=True, null=True)
    transaction = fields.BigForeignKey(Transaction, null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    # created = models.DateTimeField()

    def __unicode__(self):
        return u'%s' % self.id

    def get_created_est(self):
        return self.created

    def get_string(self):
        return 'User %s used %s: %s for customer %s. It was %s. <a href="%s">See transaction</a><br/>' % \
               (self.user, self.carrier, self.plan, self.customer, self.created, self.transaction.get_full_url())


class CommandLog(models.Model):
    id = fields.BigAutoField(primary_key=True)
    command = models.CharField(max_length=256)
    message = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

    # created = models.DateTimeField()

    def __unicode__(self):
        return u'%s' % self.command


class ImportLog(models.Model):
    id = fields.BigAutoField(primary_key=True)
    company = fields.BigForeignKey(CompanyProfile, null=True)
    command = models.CharField(max_length=256)
    message = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

    def __unicode__(self):
        return u'%s' % self.command


class ConfirmDP(models.Model):
    id = fields.BigAutoField(primary_key=True)
    login = models.CharField(max_length=256)
    password = models.CharField(max_length=256)
    confirm = models.CharField(max_length=256)
    created = models.DateTimeField(auto_now_add=True)

    # created = models.DateTimeField()

    def __unicode__(self):
        return u'%s' % self.created


class PinReport(models.Model):
    SUCCESS = 'S'
    ERROR = 'E'
    STATUS_TYPE_CHOICES = (
        (SUCCESS, 'Success'),
        (ERROR, 'Error'),
    )
    company = fields.BigForeignKey(CompanyProfile, null=True)
    report = models.TextField()
    status = models.CharField(max_length=3, choices=STATUS_TYPE_CHOICES, default=SUCCESS)
    created = models.DateTimeField(auto_now_add=True)

    def __unicode__(self):
        return u'%s' % self.id


class News(models.Model):
    CATEGORIES = (
        ('BF', 'Bug Fix'),
        ('NF', 'New Features'),
        ('IN', 'Instructions/FAQ'),
        ('OP', 'Optional Paid Futures')
    )
    title = models.CharField(max_length=150, blank=True)
    category = models.CharField(max_length=2, choices=CATEGORIES)
    message = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        for company in CompanyProfile.objects.filter(superuser_profile=False):
            company.show_updates = True
            company.save()
        super(News, self).save(*args, **kwargs)

    def __unicode__(self):
        return '[' + self.get_category_display() + '] ' + self.title

    class Meta:
        verbose_name_plural = "News"


def notify_admin(sender, instance, created, **kwargs):
    if created:
        instance.set_selling_prices()


signals.post_save.connect(notify_admin, sender=CompanyProfile)


@receiver(post_save, sender=Plan)
def set_selling_prices(instance, created, **kwargs):
    if created:
        instance.set_selling_prices()


@receiver(pre_save, sender=AutoRefill)
def set_prerefill_phone_number_to_phone_number(instance, **kwargs):
    instance.set_prerefill_phone_number_to_phone_number()


@receiver(pre_save, sender=Customer)
def set_primary_email_to_lowercase(instance, **kwargs):
    instance.set_primary_email_to_lowercase()


# @receiver(post_save, sender=Customer)
# def set_sms_email_to_first_phone_number(instance, created, **kwargs):
#     instance.set_sms_email_to_first_phone_number()


@receiver(pre_delete, sender=AutoRefill)
def set_trigger_of_all_related_transactions(instance, **kwargs):
    for transaction in Transaction.objects.filter(autorefill=instance):
        transaction.trigger = instance.get_trigger_display() + ' ' + str(instance.id)
        transaction.save()


list_of_models = (
    'CompanyProfile', 'Customer', 'Carrier', 'CarrierAdmin', 'Plan',
    'PlanDiscount', 'AutoRefill', 'CreditCardCharge', 'UnusedPin', 'User', 'Transaction'
)

@receiver(pre_save)
def logging_update(sender, instance, **kwargs):
    if sender.__name__ in list_of_models:
        company = None
        request_user = None
        user_str = "System"

        http_request = get_request()
        if http_request:
            request_user = get_request().user
            user_str = 'User %s' % request_user
            logger.debug('request_user %s' % request_user)
            if not request_user.is_authenticated():
                # if 'User AnonymousUser' == user_str:
                request_user = None
            elif request_user.profile.company:
                company = request_user.profile.company

        if instance.pk:  # if object was changed
            from django.forms.models import model_to_dict

            new_values = model_to_dict(instance)
            old_values = sender.objects.get(pk=instance.pk).__dict__
            for key in new_values.keys():
                if key not in old_values.keys():  # because there is a things in new_values that we don't need
                    new_values.pop(key, None)
            changed = [key for key in new_values.keys() if ((old_values[key] != new_values[key]) and
                                                            not ((old_values[key] is None and new_values[key] == '')
                                                                 or (old_values[key] == '' and new_values[key] is None)))]
            if len(changed) > 0:
                update = ''
                for key in changed:
                    update += key.replace('_', ' ').upper() + ': from ' + str(old_values[key]) + ' to ' + str(new_values[key]) + '; '
                note = '%s updated %s: %s \n' % (user_str, sender.__name__, str(instance))
                note += update
                Log.objects.create(user=request_user, company=company, note=note)


@receiver(post_save)
def logging_create(sender, instance, created, **kwargs):
    if sender.__name__ in list_of_models:
        from django.forms.models import model_to_dict

        company = None
        request_user = None
        user_str = "System"

        http_request = get_request()
        if http_request:
            request_user = get_request().user
            user_str = 'User %s' % request_user
            if not request_user.is_authenticated():
                # if 'User AnonymousUser' == user_str:
                request_user = None
            elif request_user.profile.company:
                company = request_user.profile.company

        if created:
            obj_attr = ""
            for key in model_to_dict(instance).keys():
                obj_attr += key.replace('_', ' ').upper() + ': ' + str(model_to_dict(instance)[key]) + '; '
            note = '%s created %s: %s \n %s' % (user_str, sender.__name__, str(instance), obj_attr)
            Log.objects.create(user=request_user, company=company, note=note)


@receiver(pre_delete)
def logging_delete(sender, instance, **kwargs):

    if sender.__name__ in list_of_models:
        from django.forms.models import model_to_dict

        company = None
        request_user = None
        user_str = "System"

        http_request = get_request()
        if http_request:
            request_user = get_request().user
            user_str = 'User %s' % request_user
            if not request_user.is_authenticated():
                # if 'User AnonymousUser' == user_str:
                request_user = None
            elif request_user.profile.company:
                company = request_user.profile.company

        obj_attr = ""
        for key in model_to_dict(instance).keys():
            obj_attr += key.replace('_', ' ').upper() + ': ' + str(model_to_dict(instance)[key]) + '; '
        note = 'User %s deleted %s: %s \n %s' % (user_str, sender.__name__, str(instance), obj_attr)
        Log.objects.create(user=request_user, company=company, note=note)


# DO NOT DELETE. IT IS FOR SIGNALS
from . import receivers
