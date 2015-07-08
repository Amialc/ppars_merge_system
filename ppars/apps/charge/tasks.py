import logging
from datetime import timedelta, datetime
import decimal

import pytz
from celery import task
from celery.task import periodic_task

from ppars.apps.core.send_notifications import \
    successful_precharge_customer_notification, \
    failed_precharge_customer_notification, \
    failed_precharge_company_notification, prepayment_customer_notification, \
    check_message_customer, check_message_company
from ppars.apps.tzones.functions import crontab_with_correct_tz
from ppars.apps.charge.models import Charge, ChargeError
from ppars.apps.core.models import AutoRefill, CompanyProfile, Customer, Plan

logger = logging.getLogger(__name__)


# description: Pre CC Charge Job 4 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=9, minute=00))
def precharge_job():
    precharge_today = []
    t = 0
    d = 1
    today = datetime.now(pytz.timezone('US/Eastern')).date()
    for company in CompanyProfile.objects.filter(superuser_profile=False):
        if company.authorize_precharge_days:
            payment_day = today + timedelta(days=company.authorize_precharge_days)
            for autorefill in AutoRefill.objects.filter(
                    enabled=True,
                    company=company,
                    renewal_date=payment_day).exclude(customer__charge_getaway=Customer.CASH):
                if (autorefill.customer.charge_getaway == Customer.DOLLARPHONE and
                            autorefill.plan.plan_type == Plan.DOMESTIC_TOPUP):
                    continue
                if not autorefill.check_renewal_end_date(today=payment_day):
                    # return self.enabled
                    # if False == not enabled then skip autorefill
                    continue
                if autorefill.pre_refill_sms and not autorefill.check_twilio_confirm_sms():
                    continue
                amount, tax = autorefill.calculate_cost_and_tax()
                need_paid = search_unused_charges(autorefill, amount)
                if need_paid:
                    if autorefill.customer.charge_getaway == Customer.CASH_PREPAYMENT:
                        prepayment_customer_notification(autorefill, need_paid)
                        continue
                    charge = autorefill.create_charge(need_paid, tax)
                    if Charge.DOLLARPHONE == charge.payment_getaway:
                        queue_precharge.apply_async(args=[charge], countdown=60*d)
                        d += 1
                    elif charge.customer.id in precharge_today:
                        t += 1
                        queue_precharge.apply_async(args=[charge], countdown=200*t)
                    else:
                        precharge_today.append(charge.customer.id)
                        queue_precharge.delay(charge)


def search_unused_charges(autorefill, amount):
    charges = Charge.objects.filter(
        customer=autorefill.customer,
        used=False,
        status='S').order_by('created')
    exist_amount = decimal.Decimal(0.0)
    # calculate sum for cost
    for charge in charges:
        if exist_amount < amount:
            exist_amount = exist_amount + (charge.amount - charge.summ)
    result = amount - exist_amount
    if result < 0:
        return False
    else:
        return result


@task
def queue_precharge(charge):
    try:
        charge.make_charge()
        charge.add_charge_step('precharge', Charge.SUCCESS, 'Charge ended successfully')
        if charge.customer.email_success:
            successful_precharge_customer_notification(charge)
    except Exception, e:
        charge.add_charge_step('precharge', Charge.ERROR, 'Charge ended with error: "%s"' % e)
        charge_error, created = ChargeError.objects.get_or_create(charge=charge, step='charge', message='%s' % e)
        if created and check_message_customer(str(e)):
            failed_precharge_customer_notification(charge)
        if charge.company.precharge_failed_email or check_message_company(str(e)):
            failed_precharge_company_notification(charge)
