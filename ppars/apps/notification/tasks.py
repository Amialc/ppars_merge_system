import calendar
import logging
import traceback
from celery.task import periodic_task, task
from datetime import timedelta, datetime, time, date
import decimal
from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils import timezone
import pytz
from ppars.apps.charge.tasks import search_unused_charges
from ppars.apps.tzones.functions import crontab_with_correct_tz
from ppars.apps.charge.models import Charge
from ppars.apps.core.models import CompanyProfile, AutoRefill, Customer, \
    Transaction, News, PhoneNumber, UserProfile
from models import Notification, SpamMessage, NewsMessage

logger = logging.getLogger('ppars')


# description: 1 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=01, minute=00))
def one_hour_job():
    unused_charges.delay()
    pre_refill_sms.delay()
    transaction_sellingprice_notification.delay()
    unpaid_transaction_notification.delay()


# description: 8 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=8, minute=00))
def eight_hour_job():
    future_charges.delay()
    insufficient_funds.delay()
    send_notifications.delay()
    news_message_job.delay()


@task
def news_message_job():
    if News.objects.filter(created__gt=datetime.now(pytz.timezone('US/Eastern')) - timedelta(hours=23, minutes=59)):
        news_email = NewsMessage.objects.create(
            title='Updates for ' + datetime.now(pytz.timezone('US/Eastern')).date().strftime("%Y-%m-%d"),
            message='')
        for news in News.objects.filter(created__gt=datetime.now(pytz.timezone('US/Eastern')) - timedelta(hours=23,
                                                                                                          minutes=59)):
            news_email.message += '<a href=\'' + settings.SITE_DOMAIN + '%s' % (reverse('news_detail',
                                                                                        args=[news.id])) + '\'>' + \
                                  news.get_category_display() + ' ' + news.title + '</a><br><p>' + news.message + '</p><br>'
        news_email.save()
        news_email.send_mandrill_email()


@task
def send_notifications():
    start_date = datetime.combine(datetime.today(), time.min)
    end_date = datetime.combine(datetime.today(), time.max)
    for notification in Notification.objects.filter(created__range=(start_date, end_date), status=None):
        logger.debug('Notification %s [%s]' % (notification.subject, notification.id))
        try:
            notification.send_notification()
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))


@task
def unused_charges():
    now = timezone.now()
    today = now.date()
    for company in CompanyProfile.objects.filter(
            superuser_profile=False,
            unused_charge_notification=True):
        subject = "[%s] Unused Credit Card Charges" % company.company_name
        charge_list = []
        for cc in Charge.objects.filter(
                status=Charge.SUCCESS,
                company=company,
                summ=decimal.Decimal(0.00),
                used=False,
                company_informed=False,
                created__lt=(today - timedelta(days=company.authorize_precharge_days + 1))).exclude(
            payment_getaway=Charge.CASH_PREPAYMENT):
            charge_list.append('<a href="%s%s">%s</a>' %
                               (settings.SITE_DOMAIN,
                                reverse('charge_detail', args=[cc.id]), cc))
            cc.company_informed = True
            cc.save()
        if charge_list:
            # 'Refund started automatically. ' \
            body = 'Hi %s,\n\nThis charges didn`t used more than %s days. ' \
                   'Please, check it.\n\n%s' \
                   '\n\nRegards, EZ-Cloud Autorefill System' % \
                   (company.company_name,
                    company.authorize_precharge_days,
                    '\n'.join(charge_list)
                    )
            notification = Notification.objects.create(
                company=CompanyProfile.objects.get(superuser_profile=True),
                email=company.email_id,
                subject=subject,
                body=body,
                send_with=Notification.MAIL)
            try:
                notification.send_notification()
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))


@task
def future_charges():
    now = timezone.now()
    today = now.date()
    for company in CompanyProfile.objects.filter(superuser_profile=False):
        payment_day = today + timedelta(days=3 + company.authorize_precharge_days)
        subject = "[%s] Credit Card Charge" % company.company_name
        if company.authorize_precharge_days:
            for autorefill in AutoRefill.objects.filter(
                    enabled=True,
                    company=company,
                    renewal_date=payment_day,
                    customer__charge_type='CC',
                    customer__precharge_sms=True
            ).exclude(customer__send_status=Customer.DONT_SEND):
                if not autorefill.check_renewal_end_date(today=payment_day):
                    # return self.enabled
                    # if False == not enabled then skip autorefill
                    continue
                cost, tax = autorefill.calculate_cost_and_tax()
                body = 'Hi %s,<br/><br/>' \
                       'We are going to charge your card on %s for $%s to ' \
                       'refill your mobile phone number %s.<br/>Please make ' \
                       'sure you have enough funds in your card.' \
                       '<br/><br/>Regards, %s' % \
                       (autorefill.customer,
                        autorefill.renewal_date,
                        cost,
                        autorefill.phone_number,
                        autorefill.company.company_name,
                        )
                notification = Notification.objects.create(
                    company=company,
                    customer=autorefill.customer,
                    email=autorefill.customer.primary_email,
                    phone_number=autorefill.phone_number,
                    subject=subject,
                    body=body,
                    send_with=autorefill.customer.send_status)
                try:
                    notification.send_notification()
                except Exception, e:
                    logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))


@task
def insufficient_funds():
    today = datetime.now(pytz.timezone('US/Eastern')).date()
    payment_day = today + timedelta(days=1)
    for company in CompanyProfile.objects.filter(superuser_profile=False, insufficient_funds_notification=True):
        unpaid_autorefills = []
        for autorefill in AutoRefill.objects.filter(
                enabled=True,
                company=company,
                renewal_date=payment_day,
                customer__charge_getaway=Customer.CASH_PREPAYMENT,
        ):
            amount, tax = autorefill.calculate_cost_and_tax()
            need_paid = search_unused_charges(autorefill, amount)
            if need_paid:
                unpaid_autorefills.append('<br/>%s needs $%s for <a href="%s">%s</a>' %
                                          (autorefill.customer,
                                           need_paid,
                                           reverse('autorefill_update', args=[autorefill.id]),
                                           autorefill.id))
        if unpaid_autorefills:
            subject = "[%s] Unpaid scheduled refills at %s " % (company.company_name, payment_day)
            body = 'Unpaid scheduled refills:%s<br/><br/>Regards, %s' % (
                ','.join(unpaid_autorefills), company.company_name)
            notification = Notification.objects.create(
                company=CompanyProfile.objects.get(superuser_profile=True),
                email=company.email_id,
                subject=subject,
                body=body,
                send_with=Notification.MAIL)
            try:
                notification.send_notification()
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))


def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month / 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@task
def send_notification_license_expiries():
    date_expiries = add_months(datetime.now(pytz.timezone('US/Eastern')).date(), 1)
    subject_user = "The license expires in a month!"
    subject_agent = "The license expires in a month for users!"
    body_user = "The license expires in a month for user %s %s, date of license expiry is %s."
    body_company = "The license expires in a month for users %s, date of license expiry is %s."
    for user in UserProfile.objects.filter(license_expiries=True,
                                           date_limit_license_expiries=date_expiries):
        if user.email:
            notification = Notification.objects.create(
                company=Notification.objects.get(company=CompanyProfile.objects.get(superuser_profile=True)),
                email=user.email,
                subject=subject_user,
                body=body_user % (user.first_name, user.last_name, user.date_limit_license_expiries),
                send_with=Notification.MAIL
            )
            try:
                notification.send_notification()
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
    for company in CompanyProfile.objects.all():
        company_users = ""
        for user in UserProfile.objects.filter(license_expiries=True,
                                               date_limit_license_expiries=date_expiries,
                                               company=company):
            company_users += "%s %s, " % (user.first_name, user.last_name)
        if company_users and company.email_sales_agent:
            notification = Notification.objects.create(
                company=Notification.objects.get(company=CompanyProfile.objects.get(superuser_profile=True)),
                email=company.email_sales_agent,
                subject=subject_agent,
                body=body_company % (company_users, date_expiries),
                send_with=Notification.MAIL,
            )
            try:
                notification.send_notification()
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))


@task
def queue_send_sms(pk):
    spam_message = SpamMessage.objects.get(id=pk)
    customers = []
    if spam_message.customer_type == 'A':
        customers = Customer.objects.filter(company=spam_message.company,
                                            group_sms=True)
    elif spam_message.customer_type == 'E':
        customers = Customer.objects.filter(company=spam_message.company,
                                            enabled=True,
                                            group_sms=True)
    elif spam_message.customer_type == 'D':
        customers = Customer.objects.filter(company=spam_message.company,
                                            enabled=False,
                                            group_sms=True)
    for customer in customers:
        notification = Notification.objects.create(
            company=spam_message.company,
            customer=customer,
            phone_number=PhoneNumber.objects.filter(customer=customer).first().number,
            subject='Global Notification',
            body=spam_message.message,
            send_with=spam_message.send_with)
        notification.send_notification()


@task
def unpaid_transaction_notification():
    today = datetime.now(pytz.timezone('US/Eastern'))
    for transaction in Transaction.objects.filter(paid=False,
                                                  status='S',
                                                  state='C',
                                                  started__range=(today - timedelta(days=1),
                                                                  today - timedelta(hours=2))) \
            .exclude(customer__sms_email=''):
        message = 'Hello %s.<br/>' \
                  'You didn`t paid for refill on %s for %s' \
                  '<br/><br/>Regards, %s' % \
                  (
                      transaction.customer.first_name,
                      transaction.started.astimezone(pytz.timezone('US/Eastern')).strftime("%m/%d/%y %H:%M:%S"),
                      transaction.cost,
                      transaction.company.company_name,
                  )
        Notification.objects.create(
            company=transaction.company,
            customer=transaction.customer,
            email=transaction.customer.primary_email,
            phone_number=transaction.autorefill.phone_number,
            subject='Unpaid refill notification',
            body=message,
            send_with=Notification.SMS_EMAIL)


@task
def pre_refill_sms():
    today = datetime.now(pytz.timezone('US/Eastern')).date()
    for autorefill in AutoRefill.objects.filter(renewal_date=today + timedelta(days=5),
                                                enabled=True,
                                                pre_refill_sms=True):
        logger.debug('pre_refill_sms %s for autorefill %s' % (today + timedelta(days=5), autorefill))
        message = 'Hello %s, your phone is scheduled to be refilled, ' \
                  'please reply \"yes\" if you need a refill for %s' \
                  '<br/><br/>Regards, %s' % \
                  (autorefill.customer.first_name,
                   autorefill.plan.get_plansellingprice(autorefill.company,
                                                        autorefill.customer.selling_price_level),
                   autorefill.company.company_name)
        Notification.objects.create(
            company=autorefill.company,
            customer=autorefill.customer,
            email=autorefill.customer.primary_email,
            phone_number=autorefill.pre_refill_sms_number,
            subject='Refill confirmation',
            body=message,
            send_with=Notification.SMS)


@task
def transaction_sellingprice_notification():
    '''
    :param schedule:
    :return: notify on email with amount of money required for scheduled transaction in three days
    '''
    for company in CompanyProfile.objects.filter(superuser_profile=False, deposit_amount_notification=True):
        week = company.sellingprices_amount_for_week()
        if week[0] + week[1] + week[2] + week[3] + week[4] + week[5] + week[6] > 0:
            email_subject = "Transaction price notification"
            email_body = "Scheduled transactions selling prices amount " \
                         "- for today: %s, for tomorrow: %s, for three days: %s, " \
                         "for four days: %s, for five days: %s, for six days: %s, " \
                         "for week: %s. Please, top up your bank account if you" \
                         " don`t have enough." % \
                         (week[0], week[1], week[2], week[3], week[4], week[5], week[6])
            notification = Notification.objects.create(
                company=CompanyProfile.objects.get(superuser_profile=True),
                email=company.email_id,
                subject=email_subject,
                body=email_body,
                send_with=Notification.MAIL
            )
            notification.send_notification()
