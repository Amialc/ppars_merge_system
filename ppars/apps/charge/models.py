import decimal
import datetime
import logging
import traceback
import authorize
from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import models
from ppars.apps.core.dollarphone import dpsite_get_pin_charge, \
    dpsite_top_up_charge
from pysimplesoap.client import SoapClient
from ppars.apps.core import fields
from ppars.apps.core.models import CompanyProfile, AutoRefill, Customer, \
    Transaction, Plan

logger = logging.getLogger('ppars')


class Charge(models.Model):
    SUCCESS = 'S'
    ERROR = 'E'
    REFUND = 'R'
    VOID = 'V'
    PROCESS = 'P'
    STATUS_TYPE_CHOICES = (
        (SUCCESS, 'Success'),
        (ERROR, 'Error'),
        (REFUND, 'Refund'),
        (VOID, 'Void'),
    )
    AUTHORIZE = 'A'
    USAEPAY = 'U'
    DOLLARPHONE = 'DP'
    CASH_PREPAYMENT = 'CP'
    CASH = 'CA'
    CHARGE_GETAWAY_CHOICES = (
        (AUTHORIZE, 'Authorize'),
        (USAEPAY, 'USAePay'),
        (DOLLARPHONE, 'DollarPhone'),
        (CASH_PREPAYMENT, 'Cash(PrePayment)'),
        (CASH, 'Cash'),
    )
    REFUND_STATUS = (
        (SUCCESS, 'Success'),
        (ERROR, 'Error'),
    )
    id = fields.BigAutoField(primary_key=True)
    company = fields.BigForeignKey(CompanyProfile, null=True)
    autorefill = fields.BigForeignKey(AutoRefill, null=True)
    customer = fields.BigForeignKey(Customer, null=True)
    company_informed = models.BooleanField(default=False)

    creditcard = models.CharField(max_length=20, blank=True)
    used = models.BooleanField(default=False)
    amount = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0))
    tax = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0))
    summ = models.DecimalField(max_digits=5, decimal_places=2, default=decimal.Decimal(0.0))
    atransaction = models.CharField(max_length=30, blank=True)
    payment_getaway = models.CharField(max_length=3, choices=CHARGE_GETAWAY_CHOICES)
    status = models.CharField(max_length=3, choices=STATUS_TYPE_CHOICES, blank=True)
    adv_status = models.CharField(max_length=500, blank=True)

    pin = models.CharField(max_length=256, blank=True)
    pin_used = models.BooleanField(default=False)

    refund_id = models.CharField(max_length=30, blank=True)
    refund_status = models.CharField(max_length=1, choices=REFUND_STATUS, blank=True)
    refunded = models.DateTimeField(null=True)

    created = models.DateTimeField("Timestamp", auto_now_add=True)
    note = models.TextField(null=True, blank='')

    def __unicode__(self):
        return u'%s' % self.id

    def get_full_url(self):
        return '%s%s' % (settings.SITE_DOMAIN, reverse('charge_detail', args=[self.pk]))

    def is_refundable(self):
        return self.created.replace(tzinfo=None) + datetime.timedelta(days=3) < datetime.datetime.now()

    def transaction_list(self):
        return TransactionCharge.objects.filter(charge=self)

    def check_getaway(self):
        charge_gateway = self.customer.get_charge_getaway
        if self.payment_getaway != charge_gateway:
            previous_getaway = self.get_payment_getaway_display()
            self.payment_getaway = charge_gateway
            self.save()
            self.add_charge_step(
                'check payment',
                ChargeStep.SUCCESS,
                'Customer getaway changed from "%s" to "%s"' %
                (previous_getaway, self.get_payment_getaway_display()))
        return self

    def make_charge(self):
        try:
            # charge card to USAePAY
            if Charge.USAEPAY == self.payment_getaway:
                if not self.customer.usaepay_customer_id:
                    raise Exception(u'Customer credit card didn\'t added to USAePay')
                self.atransaction = self.add_transaction_to_usaepay()
            # charge card to Authorize
            elif Charge.AUTHORIZE == self.payment_getaway:
                if not self.customer.authorize_id:
                    raise Exception(u'Customer credit card didn\'t added to Authorize')
                self.atransaction = self.add_transaction_to_authorize()
            elif Charge.DOLLARPHONE == self.payment_getaway:
                if not self.customer.has_local_cards:
                    raise Exception(u'Customer credit card didn\'t added to system')
                self.atransaction = self.make_dollar_phone_charge()
            self.status = Charge.SUCCESS
            self.used = False
            self.adv_status = "Charge ended successfully"
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            self.used = True
            self.status = Charge.ERROR
            self.adv_status = 'CC Charge failed with error: "%s"' % e
            raise Exception(e)
        finally:
            self.save()

    def make_dollar_phone_charge(self):
        if settings.TEST_MODE:
            return 123456
        plan = self.is_plan_available()
        form_fields = {
                'username': self.company.dollar_user,
                'password': self.company.dollar_pass,
                'Carrier': plan.carrier.name,
                'Plan': plan.plan_name,
                'company': self.company,
                'Amount': '$%s' % plan.plan_cost,
                'Customer': self.customer,
                'phone_number': self.autorefill.phone_number
        }
        if self.autorefill.plan.plan_type == Plan.DOMESTIC_TOPUP:
            receipt_id, adv_status = dpsite_top_up_charge(form_fields)
        else:
            receipt_id, adv_status, pin = dpsite_get_pin_charge(form_fields)
            if pin:
                self.pin = pin
            else:
                raise Exception("%s" % adv_status)
        self.add_charge_step('charge', Charge.SUCCESS, "%s" % adv_status)
        return receipt_id

    def is_plan_available(self):
        # check is plan available
        self.add_charge_step('get pin', Charge.SUCCESS, 'Check is plan available')
        plan = self.autorefill.plan
        if not plan.available:
            if plan.universal_plan:
                plan = plan.universal_plan
                self.add_charge_step('charge', Charge.SUCCESS,
                                      'Plan not available. Used Universal plan %s' %
                                      plan.plan_id)
            else:
                raise Exception('Plan not available and didn\'t have universal plan')
        return plan

    def add_transaction_to_authorize(self):
        if settings.TEST_MODE:
            return 123456
        self.company.authorize_authorization()
        aid = self.customer.authorize_id.split("_")
        line_items = []
        if self.autorefill:
            line_items = [{
                'item_id': '%s' % self.autorefill.plan.id,
                'name': '%s' % self.autorefill.plan,
                'description': 'Prepaid Phone recharge for %s' % self.autorefill.phone_number,
                'quantity': 1,
                'unit_price': self.amount,
                'taxable': 'false',
            }]
        d = {
            'amount': self.amount,
            'customer_id': aid[0],
            'payment_id': aid[1],
            'line_items': line_items
        }
        result = authorize.Transaction.sale(d)
        return result.transaction_response.trans_id

    def add_transaction_to_usaepay(self):
        if settings.TEST_MODE:
            return 123456
        token = self.company.usaepay_authorization()
        client = SoapClient(wsdl=settings.USAEPAY_WSDL,
                            trace=True,
                            ns=False)
        description = ''
        if self.autorefill:
            description = 'Prepaid Phone recharge for %s' % self.autorefill.phone_number
        params = {
            u'Command': 'Sale',
            u'Details': {
                u'Amount': self.amount,
                u'Description': description,
                u'Invoice': '%s' % self.id,
                # u'OrderID': '',
                # u'PONum': '',
            }
        }
        response = client.runCustomerTransaction(Token=token,
                                                 CustNum=self.customer.usaepay_customer_id,
                                                 PaymentMethodID=0,
                                                 Parameters=params)
        result_code = response['runCustomerTransactionReturn']['ResultCode']
        if 'A' == result_code:
            result = response['runCustomerTransactionReturn']['RefNum']
            return result
        elif 'D' == result_code:
            self.atransaction = response['runCustomerTransactionReturn']['RefNum']
            self.save()
            raise Exception('Transaction Declined: %s' % response['runCustomerTransactionReturn']['Error'])
        else:
            self.atransaction = response['runCustomerTransactionReturn']['RefNum']
            self.save()
            raise Exception('Transaction Error: %s' % response['runCustomerTransactionReturn']['Error'])

    def make_void(self):
        try:
            if Charge.USAEPAY == self.payment_getaway:
                response = self.void_transaction_from_usaepay()
                self.adv_status = 'CC Charge amount void to customer'
                self.status = Charge.VOID
            elif Charge.AUTHORIZE == self.payment_getaway:
                try:
                    self.adv_status = self.void_transaction_from_authorize()
                    self.status = Charge.VOID
                except authorize.exceptions.AuthorizeResponseError, e:
                    self.make_refund()
                    raise Exception(e.full_response['transaction_response']['errors'][0]['error_text'])
        except Exception, e:
            self.adv_status = 'CC Charge void failed with error: "%s"' % e
            raise Exception(e)
        finally:
            self.used = True
            self.save()

    def void_transaction_from_authorize(self):
        if settings.TEST_MODE:
            return 'TEST'
        self.company.authorize_authorization()
        result = authorize.Transaction.void(self.atransaction)
        return result.transaction_response.messages[0]['message']['description']

    def void_transaction_from_usaepay(self):
        if settings.TEST_MODE:
            return 'TEST'
        token = self.company.usaepay_authorization()
        result = ''
        if token:
            client = SoapClient(wsdl=settings.USAEPAY_WSDL,
                                trace=False,
                                ns=False)
            response = client.voidTransaction(Token=token, RefNum=self.atransaction)
            result = response['voidTransactionReturn']
        return result

    def make_refund(self):
        try:
            if Charge.USAEPAY == self.payment_getaway:
                result = self.refund_transaction_from_usaepay()
                self.adv_status = 'CC Charge amount refunded to customer.' \
                                'Please, check information in %s in 1 day' % (self.get_payment_getaway_display())
            elif Charge.AUTHORIZE == self.payment_getaway:
                try:
                    result = self.refund_transaction_from_authorize()
                    self.refund_id = result.transaction_response.trans_id
                    self.adv_status = result.transaction_response.messages[0]['message']['description']
                except authorize.exceptions.AuthorizeResponseError, e:
                    raise Exception(e.full_response['transaction_response']['errors'][0]['error_text'])
        except Exception, e:
            self.adv_status = 'CC Charge refund failed with error: "%s"' % e
            raise Exception(e)
        finally:
            self.refunded = datetime.datetime.now()
            self.used = True
            self.status = Charge.REFUND
            self.save()

    def refund_transaction_from_authorize(self):
        if settings.TEST_MODE:
            return 'TEST'
        self.company.authorize_authorization()
        result = authorize.Transaction.refund({'amount': self.amount,
                                               'transaction_id': self.atransaction,
                                               'last_four': '%s' % self.creditcard[-4:]})
        return result

    def refund_transaction_from_usaepay(self):
        if settings.TEST_MODE:
            return 'TEST'
        token = self.company.usaepay_authorization()
        client = SoapClient(wsdl=settings.USAEPAY_WSDL,
                            trace=True,
                            ns=False)
        response = client.refundTransaction(Token=token,
                                            RefNum=self.atransaction,
                                            Amount=self.amount)
        return response['refundTransactionReturn']['Result']

    def check_refund(self):
        try:
            if not self.refund_id:
                self.adv_status = 'Charge hasn\'t refund id from payment system.' \
                                  ' Please, check payment system for making manual refund or check it status'
                self.refund_status = self.SUCCESS
                return
            if self.payment_getaway == self.USAEPAY:
                pass
            elif Charge.AUTHORIZE == self.payment_getaway:
                try:
                    result = self.transaction_detail_from_authorize(self.refund_id)
                    if 'refundPendingSettlemen' in result['transaction']['transaction_status']:
                        self.refund_status = self.PROCESS
                        self.adv_status = 'Refund registered and is awaiting the payment system processing'
                    elif 'refundSettledSuccessfully' in result['transaction']['transaction_status']:
                        self.refund_status = self.SUCCESS
                        self.adv_status = 'Refund Settled Successfully'
                    else:
                        self.adv_status = result['transaction']['transaction_status']
                except authorize.exceptions.AuthorizeResponseError, e:
                    raise Exception(e.full_response.messages[0]['message']['text'])
        except Exception, e:
            self.adv_status = 'Check refund failed with error: "%s"' % e
            self.refund_status = self.ERROR
        finally:
            self.save()

    def transaction_detail_from_authorize(self, transaction_id):
        if settings.TEST_MODE:
            return 'TEST'
        self.company.authorize_authorization()
        result = authorize.Transaction.details(transaction_id)
        return result

    def add_charge_step(self, action, status, adv_status):
        ChargeStep.objects.create(
            charge=self,
            action=action,
            status=status,
            adv_status=adv_status
        )


class TransactionCharge(models.Model):
    charge = fields.BigForeignKey(Charge)
    transaction = fields.BigForeignKey(Transaction, null=True)
    amount = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    created = models.DateTimeField("Started at", auto_now_add=True)

    def __unicode__(self):
        return u'Ch %s for tr %s' % (self.charge, self.transaction)


class ChargeStep(models.Model):
    SUCCESS = 'S'
    ERROR = 'E'
    STATUS_TYPE_CHOICES = (
        (SUCCESS, 'Success'),
        (ERROR, 'Error'),
    )
    charge = fields.BigForeignKey(Charge)
    action = models.CharField(max_length=200)
    status = models.CharField(max_length=3, choices=STATUS_TYPE_CHOICES)
    adv_status = models.CharField(max_length=500, null=True)
    created = models.DateTimeField("Timestamp", auto_now=True)

    def __unicode__(self):
        return self.action


class ChargeError(models.Model):
    charge = fields.BigForeignKey(Charge)
    step = models.CharField(max_length=50)
    message = models.CharField(max_length=500)
    created = models.DateTimeField(auto_now=True)

    def __unicode__(self):
        return u'%s [%s]' % (self.step, self.created)