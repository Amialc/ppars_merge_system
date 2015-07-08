import logging
import traceback
import decimal
from datetime import datetime, timedelta, time
from django.core.urlresolvers import reverse
import pytz
from ppars.apps.core.models import Transaction, UserProfile, TransactionError, \
    Customer

logger = logging.getLogger('ppars')


class CheckPayments:

    def __init__(self, id):
        self.transaction = Transaction.objects.get(id=id)
        self.company = UserProfile.objects.get(user=self.transaction.user).company
        self.customer = self.transaction.customer
        self.transaction.current_step = 'check_payment'
        self.cost, self.tax = self.transaction.cost_calculation()

    def main(self):
        if not self.transaction.paid:
            try:
                if not self.transaction.retry_count:
                    self.transaction.need_paid = self.transaction.cost
                if self.customer.charge_getaway == Customer.DOLLARPHONE and self.transaction.pin:
                    self.transaction.adv_status = 'Customer can`t be charged on ' \
                                                  'Dollar Phone because ' \
                                                  'transaction already has pin'
                    self.transaction.add_transaction_step(
                        'check payment',
                        'not paid',
                        'E',
                        self.transaction.adv_status)
                    return self.transaction
                self.processing_charges()
                if self.transaction.need_paid:
                    if self.make_new_charge():
                        self.transaction.paid = True
                else:
                    self.transaction.paid = True
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
                self.transaction.add_transaction_step('check payment', 'error', 'E', u'%s' % e)
                raise Exception(u'%s' % e)
            finally:
                self.transaction.save()
        return self.transaction

    def processing_charges(self):
        charges = self.get_unused_dp_charges()
        if charges:
            if self.processing_exist_dp_charges(charges):
                return True
        charges, enough = self.get_unused_customer_charges()
        self.processing_exist_charges(charges, enough)
        return True

    def get_unused_dp_charges(self):
        from ppars.apps.charge.models import Charge
        charges = []
        for charge in Charge.objects.filter(
                customer=self.customer,
                used=False,
                status=Charge.SUCCESS,
                payment_getaway=Charge.DOLLARPHONE,
                pin_used=False,
                autorefill__plan__plan_id=self.transaction.autorefill.plan.plan_id).order_by('created'):
            if charge.summ:
                continue
            else:
                charges.append(charge)
        return charges

    def processing_exist_dp_charges(self, charges):
        from ppars.apps.charge.models import TransactionCharge, ChargeStep
        for charge in charges:
            if charge.pin:
                self.transaction.pin = charge.pin
                self.transaction.add_transaction_step(
                    'check payment',
                    'use payment',
                    'S',
                    'Use charge <a href="%s">%s</a>' %
                    (reverse('charge_detail', args=[charge.id]), charge.id))
                self.transaction.add_transaction_step(
                    'get pin',
                    'end',
                    'S',
                    'Pin %s extracted from Dollar Phone, details are at ' \
                    '<a target="blank" href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>.' %
                    (charge.pin, charge.atransaction))
                charge.summ = self.transaction.need_paid
                charge.used = True
                charge.pin_used = True
                charge.save()
                self.transaction.need_paid = decimal.Decimal(0.0)
                TransactionCharge.objects.get_or_create(charge=charge, transaction=self.transaction, defaults={'amount': self.transaction.need_paid})
                charge.add_charge_step('check payment', ChargeStep.SUCCESS, 'Full charge used for <a href="%s">%s</a>' % (reverse('transaction_detail', args=[self.transaction.id]), self.transaction.id))
                return True
        return False

    def get_unused_customer_charges(self):
        # TODO need to set via self.transaction.autorefill.renewal_date
        today = datetime.now(pytz.timezone('US/Eastern')).date()
        payment_day = today - timedelta(days=self.company.authorize_precharge_days)
        from ppars.apps.charge.models import Charge
        charges = Charge.objects.filter(customer=self.customer,
                                        used=False,
                                        status='S').exclude(payment_getaway=Charge.DOLLARPHONE, created__gt=payment_day).order_by('created')
        amount = decimal.Decimal(0.0)
        use_charges = []
        # calculate sum for cost
        enough = False
        for charge in charges:
            if amount < self.transaction.need_paid:
                amount = amount + (charge.amount - charge.summ)
                use_charges.append(charge)
        if amount >= self.transaction.need_paid:
                enough = True
        return use_charges, enough

    def processing_exist_charges(self, use_charges, enough):
        if not enough and self.customer.charge_getaway in [Customer.DOLLARPHONE, Customer.CASH, Customer.CASH_PREPAYMENT]:
            return True
        from ppars.apps.charge.models import TransactionCharge, ChargeError, ChargeStep
        # logic for calculation charges for transaction
        for use_charge in use_charges:
            if self.transaction.need_paid == use_charge.amount - use_charge.summ:
                use_charge.summ = use_charge.summ + self.transaction.need_paid
                TransactionCharge.objects.get_or_create(charge=use_charge, transaction=self.transaction, defaults={'amount': self.transaction.need_paid})
                use_charge.add_charge_step('check payment', ChargeStep.SUCCESS, '$%s from charge used for <a href="%s">%s</a>' % (self.transaction.need_paid, reverse('transaction_detail', args=[self.transaction.id]), self.transaction.id))
                self.transaction.need_paid = decimal.Decimal(0.0)
                use_charge.used = True
            elif self.transaction.need_paid < use_charge.amount - use_charge.summ:
                use_charge.summ = use_charge.summ + self.transaction.need_paid
                TransactionCharge.objects.get_or_create(charge=use_charge, transaction=self.transaction, defaults={'amount': self.transaction.need_paid})
                use_charge.add_charge_step('check payment', ChargeStep.SUCCESS, '$%s from charge used for <a href="%s">%s</a>' % (self.transaction.need_paid, reverse('transaction_detail', args=[self.transaction.id]), self.transaction.id))
                self.transaction.need_paid = decimal.Decimal(0.0)
            elif self.transaction.need_paid > use_charge.amount - use_charge.summ:
                TransactionCharge.objects.get_or_create(charge=use_charge, transaction=self.transaction, defaults={'amount': self.transaction.need_paid})
                use_charge.add_charge_step('check payment', ChargeStep.SUCCESS, '$%s from charge used for <a href="%s">%s</a>' % (use_charge.amount - use_charge.summ, reverse('transaction_detail', args=[self.transaction.id]), self.transaction.id))
                self.transaction.need_paid = self.transaction.need_paid - (use_charge.amount - use_charge.summ)
                use_charge.summ = use_charge.amount
                use_charge.used = True
            self.transaction.add_transaction_step('check payment', 'use payment', 'S', 'Use charge <a href="%s">%s</a>' % (reverse('charge_detail', args=[use_charge.id]), use_charge.id))
            use_charge.save()
            if use_charge.pin and not use_charge.pin_used:
                self.transaction.add_transaction_step('check payment', 'use payment', 'E', 'Please, remember to add <a href="%s?plan=%s&pin=%s">pin</a> to unused' % (reverse('unusedpin_create'), use_charge.autorefill.plan.id, use_charge.pin))
            for charge_error in ChargeError.objects.filter(charge=use_charge):
                TransactionError.objects.create(transaction=self.transaction, step=self.transaction.current_step, message=charge_error.message)

    def make_new_charge(self):
        from ppars.apps.charge.models import TransactionCharge, ChargeStep, Charge
        if self.transaction.customer.charge_getaway == Customer.CASH_PREPAYMENT:
            self.transaction.add_transaction_step('check payment', 'Insufficient funds', 'S', 'Insufficient funds. Transaction needs $%s more' % self.transaction.need_paid)
            self.transaction.status = Transaction.SUCCESS
            self.transaction.state = Transaction.COMPLETED
            self.transaction.adv_status = "Autorefill transaction ended. Insufficient funds. Transaction needs $%s" % self.transaction.need_paid
            return False

        if self.transaction.customer.charge_getaway == Customer.CASH:
            return False

        self.search_declined_charge()
        self.transaction.add_transaction_step('check payment', 'create payment', 'S', 'Requesting CC charge for customer.')
        new_charge = self.previous_charge()
        if new_charge:
            new_charge.amount = self.transaction.need_paid
            new_charge.add_charge_step('charge', ChargeStep.SUCCESS, 'Charge retrying from <a href="%s">%s</a>' % (reverse('transaction_detail', args=[self.transaction.id]), self.transaction.id))
        else:
            new_charge = self.transaction.autorefill.create_charge(self.transaction.need_paid, self.tax)

        payment_gateway_old = new_charge.payment_getaway
        new_charge = new_charge.check_getaway()

        try:
            self.transaction.add_transaction_step('check payment', 'use payment', 'S', 'Use charge <a href="%s">%s</a>' % (reverse('charge_detail', args=[new_charge.id]), new_charge.id))

            if new_charge.payment_getaway == Charge.DOLLARPHONE and self.transaction.retry_count == 1:

                if self.transaction.customer.authorize_id:
                    new_charge.payment_getaway = Charge.AUTHORIZE
                    new_charge.add_charge_step('charge', Charge.SUCCESS, 'Next charge will be with Authorize.')
                    self.transaction.add_transaction_step('check payment',
                                                          'change payment',
                                                          Transaction.SUCCESS,
                                                          'Payment processing will be changed to Authorize.')

                elif self.transaction.customer.usaepay_customer_id:
                    new_charge.payment_getaway = Charge.USAEPAY
                    new_charge.add_charge_step('charge', Charge.SUCCESS, 'Charge will be with USAePay.')
                    self.transaction.add_transaction_step('check payment',
                                                          'change payment',
                                                          Transaction.SUCCESS,
                                                          'Payment processing will be changed to USAePay.')

            if (payment_gateway_old == Charge.AUTHORIZE or payment_gateway_old == Charge.USAEPAY) \
                    and self.transaction.retry_count == 2:

                if self.transaction.customer.authorize_id and payment_gateway_old == Charge.USAEPAY:
                    new_charge.payment_getaway = Charge.AUTHORIZE
                    new_charge.add_charge_step('charge', Charge.SUCCESS, 'Charge will be with Authorize.')
                    self.transaction.add_transaction_step('check payment',
                                                          'change payment',
                                                          Transaction.SUCCESS,
                                                          'Payment processing will be changed to Authorize.')

                elif self.transaction.customer.usaepay_customer_id and payment_gateway_old == Charge.AUTHORIZE:
                    new_charge.payment_getaway = Charge.USAEPAY
                    new_charge.add_charge_step('charge', Charge.SUCCESS, 'Charge will be with USAePay.')
                    self.transaction.add_transaction_step('check payment',
                                                          'change payment',
                                                          Transaction.SUCCESS,
                                                          'Payment processing will be changed to USAePay.')

            new_charge.make_charge()
            new_charge.add_charge_step('precharge', ChargeStep.SUCCESS, 'Charge ended successfully')
        except Exception, e:
            new_charge.add_charge_step('charge', ChargeStep.ERROR, 'Charge ended with error: "%s"' % e)
            raise Exception(e)
        if new_charge.pin:
            self.transaction.pin = new_charge.pin
            new_charge.pin_used = True
            self.transaction.add_transaction_step(
                'get pin',
                'end',
                'S',
                'Pin %s extracted from Dollar Phone, details are at ' \
                '<a target="blank" href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>.' % \
                (new_charge.pin, new_charge.atransaction))
        new_charge.summ = self.transaction.need_paid
        new_charge.used = True
        new_charge.save()
        new_charge.add_charge_step('check payment', ChargeStep.SUCCESS, '$%s from charge used for <a href="%s">%s</a>' % (self.transaction.need_paid, reverse('transaction_detail', args=[self.transaction.id]), self.transaction.id))
        self.transaction.need_paid = decimal.Decimal(0.0)
        TransactionCharge.objects.get_or_create(charge=new_charge, transaction=self.transaction, defaults={'amount': self.transaction.need_paid})
        return True

    def search_declined_charge(self):
        from ppars.apps.charge.models import Charge
        today = datetime.now(pytz.timezone('US/Eastern')).date()
        start_date = datetime.combine(today - timedelta(days=self.transaction.company.authorize_precharge_days + 1), time.min)
        charges = []
        ccc = Charge.objects.filter(customer=self.customer, autorefill=self.transaction.autorefill, created__gt=start_date, adv_status__icontains='decline')
        if ccc:
            for charge in ccc:
                charges.append('<a href="%s">%s</a>' % (reverse('charge_detail', args=[charge.id]), charge.id))
            self.transaction.add_transaction_step('check payment', 'use payment', 'S', 'Found declined charge: %s' % ', '.join(charges))

    def previous_charge(self):
        from ppars.apps.charge.models import Charge
        today = datetime.now(pytz.timezone('US/Eastern')).date()
        return Charge.objects.filter(customer=self.customer,
                                     autorefill=self.transaction.autorefill,
                                     created__gt=datetime.combine(today-timedelta(days=3), datetime.min.time()),
                                     status=Charge.ERROR).first()