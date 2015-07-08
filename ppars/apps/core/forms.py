import hashlib
import authorize
import logging
import time
from datetime import datetime, timedelta
from django import forms
from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils.safestring import mark_safe
from django.contrib.auth.models import User
from django.utils.translation import ugettext_lazy as _
import pytz
from ppars.apps.core import models
#DO NOT DELETE THIS IMPORT
from ppars.apps.core.models import CompanyProfile
from ppars.apps.price.models import SellingPriceLevel
from ppars.apps.notification.models import SmsEmailGateway
from pysimplesoap.client import SoapClient

logger = logging.getLogger('ppars')


def f(x):
    return (x, x)


def expiration_year_choices():
    eyc = tuple(map(f, (xrange(datetime.now().year, datetime.now().year+10))))
    return eyc


class CompanyProfileForm(forms.ModelForm):
    class Meta:
        model = models.CompanyProfile
        exclude = ['customer_limit']


class CustomerForm(forms.ModelForm):
    EXPIRATION_MONTH_CHOICES = (
        (1, '01'),
        (2, '02'),
        (3, '03'),
        (4, '04'),
        (5, '05'),
        (6, '06'),
        (7, '07'),
        (8, '08'),
        (9, '09'),
        (10, '10'),
        (11, '11'),
        (12, '12'),
    )
    card_number_update = forms.CharField(widget=forms.HiddenInput(), required=False)
    card_date_update = forms.CharField(widget=forms.HiddenInput(), required=False)
    card_number = forms.CharField(required=False)
    expiration_month = forms.ChoiceField(choices=EXPIRATION_MONTH_CHOICES, required=False)
    expiration_year = forms.ChoiceField(choices=expiration_year_choices(), required=False)
    cvv = forms.CharField(required=False)
    local_card = forms.CharField(required=False)
    phone_numbers = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super(CustomerForm, self).__init__(*args, **kwargs)

    # def clean_primary_email(self):
    #     data = self.cleaned_data['primary_email'].strip()
    #     data1 = data.lower()
    #     if data1 != '':
    #         if self.instance.id:
    #             customers = models.Customer.objects.exclude(id=self.instance.id).filter(primary_email_lowercase=data1, company=self.instance.company)
    #
    #         else:
    #             customers = models.Customer.objects.filter(primary_email_lowercase=data1, company=self.instance.company)
    #         if customers.exists():
    #             msg = []
    #             for customer in customers:
    #                msg.append("Customer %s (%scustomer/%s/) already used this e-mail" % ( customer, settings.SITE_DOMAIN, customer.id))
    #             raise forms.ValidationError(msg)
    #     return data
    #
    def clean_phone_numbers(self):
        data = self.cleaned_data['phone_numbers'].strip()
        if data != '':
            data1 = data.split(',')
            for number in data1:
                if data1.count(number) > 1:
                    raise forms.ValidationError("There are more than one same phone numbers")
        return data

    def _usaepay_structured_data(self, cleaned_data):
        if len(cleaned_data['expiration_month']) == 1:
            month = '0%s' % cleaned_data['expiration_month']
        else:
            month = cleaned_data['expiration_month']
        data = {
                u'Amount': '0.00',
                u'BillingAddress': {
                    u'City': cleaned_data['city'],
                    # u'Company': '',
                    u'Country': 'US',
                    u'Email': cleaned_data['primary_email'],
                    # u'Fax': '',
                    u'FirstName': self.instance.first_name,
                    u'LastName': cleaned_data['last_name'],
                    u'Phone': cleaned_data['phone_numbers'],
                    u'State': cleaned_data['state'],
                    u'Street': cleaned_data['address'],
                    # u'Street2': '',
                    u'Zip': cleaned_data['zip'],
                },
                # u'CustNum': 'C1957486753',
                # u'CustomData': 'YToxOntzOjY6Im15ZGF0YSI7czozMDoiV2UgY291bGQgcHV0IGFueXRoaW5nIGluIGhlcmUhIjt9',
                # u'CustomFields': {
                #     u'item': {
                #         u'Field': 'Foo',
                #         u'Value': 'Testing',
                #     },
                #     u'item': {
                #         u'Field': 'Bar',
                #         u'Value': 'Tested',
                #     },
                # },
                # u'CustomerID': '730163741',
                # u'Description': 'Weekly Bill',
                u'Enabled': 'false',
                u'Next': '%s' % datetime.now(pytz.timezone('US/Eastern')).date(),
                u'Notes': 'Customer from PPARS System',
                # u'NumLeft': '50',
                # u'OrderID': '1621046782',
                u'PaymentMethods': {
                    u'item': {
                        u'CardExpiration': '%s%s' % (month, cleaned_data['expiration_year'][-2:]),
                        u'CardNumber': cleaned_data['card_number'],
                        u'CardCode': cleaned_data['cvv'],
                        u'MethodName': 'My Visa',
                        u'SecondarySort': '1',
                    },
                },
                # u'ReceiptNote': 'addCustomer test Created Charge',
                u'Schedule': 'monthly',
                u'SendReceipt': 'false',
                u'Source': 'Recurring',
                # u'Tax': '0',
                # u'User': 'TestUser',
                # u'URL': 'http://www.acme.com',
                }
        return data

    def _create_card_to_usaepay(self, cleaned_data):
        data = self._usaepay_structured_data(cleaned_data)
        seed = time.time()
        clear = '%s%s%s' % (self.request.user.get_company_profile().usaepay_source_key, seed, self.request.user.get_company_profile().usaepay_pin)
        m = hashlib.sha1()
        m.update(clear)
        token = {
                u'ClientIP':'192.168.0.1',
                    u'PinHash': {
                        u'HashValue': m.hexdigest(),
                        u'Seed': seed,
                        u'Type': 'sha1'
                    },
                u'SourceKey': self.request.user.get_company_profile().usaepay_source_key
                }
        client = SoapClient(wsdl=settings.USAEPAY_WSDL,
                            trace=False,
                            ns=False)
        response = client.addCustomer(Token=token, CustomerData=data)
        return response['addCustomerReturn']

    def _authorize_structured_data(self, cleaned_data):
        addr = {
            'first_name': cleaned_data['first_name'],
            'last_name': cleaned_data['last_name'],
            'address': cleaned_data['address'],
            'city': cleaned_data['city'],
            'state': cleaned_data['state'],
            'zip': cleaned_data['zip'],
            'country': 'US',
            'company': '',
            'phone_number': cleaned_data['phone_numbers'][:21],
            'fax_number': '',
        }
        card = {
            'card_number': cleaned_data['card_number'],
            'expiration_month': cleaned_data['expiration_month'],
            'expiration_year': cleaned_data['expiration_year'],
            'card_code': cleaned_data['cvv'],
        }
        customer = {
            'email': cleaned_data['primary_email'],
            'customer_type': 'individual',
            'shipping': addr,
            'billing': addr,
            'credit_card': card
        }
        return customer

    def _authorize_updated_customer(self, cleaned_data):
        customer = {
            'email': cleaned_data['primary_email'],
            'customer_type': 'individual',
            'description': '',
        }
        return customer

    def _authorize_updated_address(self, cleaned_data):
        address = {
            'first_name': cleaned_data['first_name'],
            'last_name': cleaned_data['last_name'],
            'address': cleaned_data['address'],
            'city': cleaned_data['city'],
            'state': cleaned_data['state'],
            'zip': cleaned_data['zip'],
            'country': 'US',
            'company': '',
            'phone_number': cleaned_data['phone_numbers'][:21],
            'fax_number': '',
        }
        return address

    def _create_card_to_authorize(self, customer_data):
        self.request.user.get_company_profile().authorize_authorization()
        result = authorize.Customer.create(customer_data)
        return result.customer_id + "_" + result.payment_ids[0]

    def _usaepay_updated_data(self, cleaned_data):
        data = {
            u'UpdateData'[0]: {
                u'Field': 'City',
                u'Value': cleaned_data['city'],
                },
            u'UpdateData'[1]: {
                u'Field': 'Email',
                u'Value': cleaned_data['primary_email'],
                },
            u'UpdateData'[2]: {
                u'Field': 'FirstName',
                u'Value': cleaned_data['first_name'],
                },
            u'UpdateData'[3]: {
                u'Field': 'LastName',
                u'Value': cleaned_data['last_name'],
                },
            u'UpdateData'[4]: {
                u'Field': 'Phone',
                u'Value': cleaned_data['phone_numbers'],
                },
            u'UpdateData'[5]: {
                u'Field': 'State',
                u'Value': cleaned_data['state'],
                },
            u'UpdateData'[6]: {
                u'Field': 'Address',
                u'Value': cleaned_data['address'],
                },
            u'UpdateData'[7]: {
                u'Field': 'Zip',
                u'Value': cleaned_data['zip'],
                },
        }
        return data

    def _usaepay_updated_card(self, cleaned_data, card_number_update, card_date_update):
        data = {}
        if card_date_update:
            if len(cleaned_data['expiration_month']) == 1:
                month = '0%s' % cleaned_data['expiration_month']
            else:
                month = cleaned_data['expiration_month']
            data[u'UpdateData'[0]] = {
                u'Field': 'CardExp',
                u'Value': '%s%s' % (month, cleaned_data['expiration_year'][-2:]),
                }
        if card_number_update:
            data['UpdateData'[1]] = {
                u'Field': 'CardNumber',
                u'Value': cleaned_data['card_number'],
                }
        return data

    def _authorize_updated_card(self, cleaned_data, card_number_update, card_date_update):
        data = {}
        if card_date_update:
            if len(cleaned_data['expiration_month']) == 1:
                month = '0%s' % cleaned_data['expiration_month']
            else:
                month = cleaned_data['expiration_month']
            data[u'expiration_month'] = month
            data[u'expiration_year'] = cleaned_data['expiration_year']
            data[u'card_code'] = cleaned_data['cvv']
        else:
            data[u'expiration_month'] = self.instance.get_local_card().expiration_month
            data[u'expiration_year'] = self.instance.get_local_card().expiration_year
            data[u'card_code'] = self.instance.get_local_card().cvv
        if card_number_update:
            data[u'card_number'] = cleaned_data['card_number']
        else:
            data[u'card_number'] = self.instance.get_local_card().number
        data[u'billing'] = self._authorize_updated_address(cleaned_data)
        return data

    def check_duplicate_number(self, data):
        if 'phone_numbers' in data and data['phone_numbers']:
            pns = []
            numbers = data['phone_numbers'].split(',')
            company = data['company']
            for number in numbers:
                if self.instance.id:
                    phonenumbers = models.PhoneNumber.objects.exclude(customer=None).exclude(customer_id=self.instance.id).filter(number=number, company=company)
                else:
                    phonenumbers = models.PhoneNumber.objects.exclude(customer=None).filter(number=number, company=company)
                if phonenumbers.exists():
                    pns.append(phonenumbers)

            if len(pns) > 0 and company.block_duplicate_phone_number:
                msg = []
                for phonenumbers in pns:
                    for phonenumber in phonenumbers:
                        msg.append(mark_safe("Number '%s' already exist at <a href=\"%s\">%s</a>" % (phonenumber.number, reverse('customer_update', args=[phonenumber.customer.id]), phonenumber.customer)))
                raise forms.ValidationError(msg)

            if self.instance.id:
                phones = models.PhoneNumber.objects.filter(customer_id=self.instance.id)
                deleted_phones = map(str, phones.exclude(number__in=numbers).values_list('number', flat=True))
                if deleted_phones:
                    prerefiled_days = company.authorize_precharge_days or 0 #0 if company.authorize_precharge_days is Blank
                    prerefiled = models.AutoRefill.objects.exclude(enabled=False).filter(phone_number__in=deleted_phones, renewal_date__gt=datetime.now() - timedelta(days=prerefiled_days))
                    if prerefiled:
                        msg = []
                        for phone in prerefiled:
                            msg.append("Cannot delete number %s because it can has precharge before top up" % phone.phone_number)
                        raise forms.ValidationError(msg)
            self.instance.set_phone_numbers(numbers)

    def clean(self):
        self.message = ''
        cleaned_data = super(CustomerForm, self).clean()
        self.check_duplicate_number(cleaned_data)
        cleaned_data['creditcard'] = self.instance.creditcard
        cleaned_data['authorize_id'] = self.instance.authorize_id
        cleaned_data['usaepay_customer_id'] = self.instance.usaepay_customer_id
        card_number_update = cleaned_data.get('card_number_update') == 'True'
        card_date_update = cleaned_data.get('card_date_update') == 'True'
        save_ccinfo = False
        if cleaned_data['charge_type'] == 'CC':
            if not self.instance.has_local_cards and not(card_date_update and card_number_update):
                self.message = '%sTo update CVC please  re-enter the credit card number<br/>' % self.message
            if card_date_update:
                save_ccinfo = True
                if cleaned_data['cvv']:
                    if not cleaned_data['cvv'].isdigit():
                        self._errors['cvv'] = self.error_class(['CVV must be digits!'])
                        del cleaned_data["cvv"]
                        save_ccinfo = False
                    elif len(cleaned_data['cvv']) not in [3, 4]:
                        self._errors['cvv'] = self.error_class(['CVV must have either three or four digits!'])
                        del cleaned_data["cvv"]
                        save_ccinfo = False
            else:
                cleaned_data['expiration_year'] = None
                cleaned_data['expiration_month'] = None
                cleaned_data["cvv"] = None
            if card_number_update:
                cleaned_data['creditcard'] = '%sXXXXXXXXXX%s' % (str(cleaned_data['card_number'])[:2], str(cleaned_data['card_number'])[-4:])
                save_ccinfo = True
                if not cleaned_data['card_number'].isdigit():
                    self._errors['card_number'] = self.error_class(['Card number must be digits!'])
                    del cleaned_data["card_number"]
                    save_ccinfo = False
            else:
                cleaned_data["card_number"] = None
            try:
                # update non payments data on UsaePay storage
                if self.instance.usaepay_customer_id:
                    data = self._usaepay_updated_data(cleaned_data)
                    if self.instance.update_card_to_usaepay(data):
                        self.message = '%sUSAePay customer data: updating successfully<br/>' % self.message
                    else:
                        self.message = '%sUSAePay customer data: updating unsuccessfully<br/>' % self.message
            except Exception, e:
                self.message = '%sFailed to save on USAePay: "%s"<br/>' % (self.message, e)
            try:
                # update non payments data on Authorize storage
                if self.instance.authorize_id:
                    self.request.user.get_company_profile().authorize_authorization()
                    data = self._authorize_updated_customer(cleaned_data)
                    aid = self.instance.authorize_id.split("_")
                    authorize.Customer.update(aid[0], data)
                    result = authorize.Customer.details(aid[0])
                    address_id = result.profile.addresses[0]['address_id']
                    address = self._authorize_updated_address(cleaned_data)
                    result = authorize.Address.update(aid[0], address_id, address)
                    self.message = '%sAuthorize customer data: updating %s<br/>' % (self.message, result.messages[0]['message']['text'])
            except Exception, e:
                self.message = '%sFailed to save on Authorize: "%s"<br/>' % (self.message, e)
        if save_ccinfo:
            # save card to local storage
            if self.instance.has_local_cards or \
                    (cleaned_data['card_number'] and
                         cleaned_data['expiration_year'] and
                         cleaned_data['expiration_month']):
                cleaned_data['local_card'] = self.instance.save_local_card(
                    number=cleaned_data["card_number"],
                    cvv=cleaned_data["cvv"],
                    year=cleaned_data['expiration_year'],
                    month=cleaned_data['expiration_month'],
                ).id
            #save card to UsaEPay storage
            try:
                if self.instance.usaepay_customer_id:
                    data = self._usaepay_updated_card(cleaned_data, card_number_update, card_date_update)
                    if self.instance.update_card_to_usaepay(data):
                        self.message = '%sUsaePay card data: updating successfully<br/>' % self.message
                    else:
                        self.message = '%sUsaePay card data: updating unsuccessfully<br/>' % self.message
                else:
                    cleaned_data['usaepay_customer_id'] = self._create_card_to_usaepay(cleaned_data)
            except Exception, e:
                self.message = '%sFailed to save credit card to UsaePay: "%s"<br/>' % (self.message, e)
                logger.error('USAEPAY Exception: %s' % e)
            # save card to Authorize storage
            try:
                if self.instance.authorize_id:
                    aid = self.instance.authorize_id.split("_")
                    card_data = self._authorize_updated_card(cleaned_data, card_number_update, card_date_update)
                    result = authorize.CreditCard.update(aid[0], aid[1], card_data)
                    self.message = '%sAuthorize card data: updating %s<br/>' % (self.message, result.messages[0]['message']['text'])
                else:
                    customer_data = self._authorize_structured_data(cleaned_data)
                    cleaned_data['authorize_id'] = self._create_card_to_authorize(customer_data)
            except Exception, e:
                self.message = '%sFailed to save credit card to Authorize: "%s"' % (self.message, e)
                logger.error('AUTHORIZE Exception: %s' % e)
                del cleaned_data["card_number"]
                return cleaned_data
        return cleaned_data

    class Meta:
        model = models.Customer
        exclude = ('user', 'usaepay_custid', 'primary_email_lowercase')


class AutoRefillForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super(AutoRefillForm, self).__init__(*args, **kwargs)
        self.fields['customer'].queryset = models.Customer.objects.filter(company=user.profile.company, enabled=True)
        self.company = user.profile.company

    def clean(self):
        cleaned_data = super(AutoRefillForm, self).clean()
        if self.company.block_duplicate_schedule:
            if self.instance.id:
                if not cleaned_data['enabled']:
                    return cleaned_data
                autorefills = models.AutoRefill.objects.exclude(id=self.instance.id).filter(
                    trigger='SC',
                    enabled=True,
                    plan=cleaned_data['plan'],
                    phone_number=cleaned_data['phone_number'],
                    company=self.instance.company)
            else:
                autorefills = models.AutoRefill.objects.filter(
                    trigger='SC',
                    enabled=True,
                    plan=cleaned_data['plan'],
                    phone_number=cleaned_data['phone_number'],
                    company=self.company)
            if autorefills.exists():
                    msg = ("A shedule refill for this number with this plan already exist.")
                    raise forms.ValidationError(msg)
        return cleaned_data

    class Meta:
        model = models.AutoRefill
        exclude = ('user', 'company', 'trigger', 'refill_type', 'last_renewal_status', 'last_renewal_date')


class ManualRefillForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super(ManualRefillForm, self).__init__(*args, **kwargs)
        self.fields['customer'].queryset = models.Customer.objects.filter(company=user.profile.company, enabled=True)
        #self.customer = args[0]
        #self.phone_number = args[1]

    class Meta:
        model = models.AutoRefill
        exclude = ('user',)


class UnusedPinForm(forms.ModelForm):
    class Meta:
        model = models.UnusedPin
        exclude = ('user', 'company')


class UnusedPinImportForm(forms.Form):
    plan = forms.CharField(required=False)
    file = forms.FileField(required=False)
    cache_id = forms.CharField(required=False)
    notes = forms.CharField(required=False)
    confirm = forms.CharField(widget=forms.HiddenInput())


class CarrierForm(forms.ModelForm):
    class Meta:
        model = models.Carrier
        exclude = ('user',)


class GenericImportForm(forms.Form):
    file = forms.FileField(required=False)
    cache_id = forms.CharField(required=False)
    confirm = forms.CharField(widget=forms.HiddenInput())


class PhoneNumberImportForm(forms.Form):
    file = forms.FileField(required=True)

    def clean_file(self):
        data = self.cleaned_data['file']
        data1 = str(data)
        if data1[-3:] != 'xls' and data1[-4:] != 'xlsx':
            raise forms.ValidationError("Unsupported file format. Please, use xls or xlsx format.")
        return data


class PlanForm(forms.ModelForm):
    class Meta:
        model = models.Plan
        exclude = ('user',)


class CarrierAdminForm(forms.ModelForm):

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(CarrierAdminForm, self).__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super(CarrierAdminForm, self).clean()
        # 1 dealer site for 1 company
        if models.CarrierAdmin.objects.filter(carrier=cleaned_data['carrier'], company=self.request.user.profile.company).exists() and self.instance.carrier_id != cleaned_data['carrier'].id:
            self._errors["carrier"] = self.error_class(['A dealer site already exists for this carrier.'])
            del cleaned_data["carrier"]
        return cleaned_data

    class Meta:
        model = models.CarrierAdmin
        exclude = ('user', 'company')


class PlanDiscountForm(forms.ModelForm):

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super(PlanDiscountForm, self).__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super(PlanDiscountForm, self).clean()
        if models.PlanDiscount.objects.filter(carrier=cleaned_data['carrier'], plan=cleaned_data['plan'], user=self.request.user).exists():
            if not (self.instance.carrier_id == cleaned_data['carrier'].id and self.instance.plan == cleaned_data['plan']):
                self._errors["carrier"] = self.error_class(['A plan discount already exists for this carrier and plan.'])
                del cleaned_data["carrier"]
        return cleaned_data

    class Meta:
        model = models.PlanDiscount
        exclude = ('user',)


class CommandForm(forms.Form):
    COMMAND_CHOICES = (
        # ('dn', "delete space in phone number from customers"),
        # ('ed', 'collect autorefill with empty date'),
        # ('go', 'get all SC order id'),
        # ('lm', 'create field with email in lowercase'),
        # ('pn', 'create model with phone number for customer'),
        # ('rb', 'change renewal interval in autorefil to blank'),
        # ('ur', 'add company profile to autorefill and transaction'),
        # ('sp', 'add selling price to plan'),
        # (11, 'add company profile to UnusedPin, Carrier and etc'),
        # ('cc', 'add company to precharge'),
        # ('up', 'add universal pocket for Red Pocket'),
        ('ts', 'transactions without sc order '),
        ('tp', 'transactions without charge '),
        ('se', 'Make customer with uniq email'),
        ('ad', 'Check autorazi db'),
        ('er', 'error charges make used'),
        ('ut', 'unpaid customer transaction from october to december'),
        # ('uc', 'Get unused charge more 3 days before')

    )
    command = forms.ChoiceField(choices=COMMAND_CHOICES)


class ConfirmDPForm(forms.Form):
    cache_id = forms.CharField(required=False)
    success = forms.CharField(widget=forms.HiddenInput())
    login = forms.CharField(required=False)
    password = forms.CharField(required=False)
    confirm = forms.CharField(required=False)

    class Meta:
        model = models.ConfirmDP