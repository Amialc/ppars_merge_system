import functools
import logging
from datetime import datetime, timedelta, time
import traceback

from django.conf import settings
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
import pytz
import requests
from BeautifulSoup import BeautifulSoup
from requests.auth import HTTPBasicAuth
from django.utils import timezone
from celery import task
from celery.task import periodic_task
from django_redis import get_redis_connection

from ppars.apps.notification.models import Notification
from ppars.apps.tzones.functions import crontab_with_correct_tz
from ppars.apps.core.prerefill import PreRefill
from ppars.apps.core.send_notifications import \
    failed_check_refunds_customer_notification
from pysimplesoap.client import SoapClient
from ppars.apps.core.refill import Refill
import ext_lib
from models import UserProfile, Customer, AutoRefill, Transaction, UnusedPin, \
    ImportLog, CompanyProfile, PhoneNumber, PinReport, Plan

logger = logging.getLogger(__name__)


# US/Eastern is -4 h from GMT
# cron:
#
# - description: Scheduled Refill Midnight Job
#   url: /cron/scheduled-refill?schedule=MN
#   schedule: every day 23:59
#   timezone: America/New_York
#
# - description: Scheduled Refill Midday Job
#   url: /cron/scheduled-refill?schedule=MD
#   schedule: every day 12:00
#   timezone: America/New_York
#
# - description: Scheduled Refill 12:01 AM Job
#   url: /cron/scheduled-refill?schedule=1201AM
#   schedule: every day 00:01
#   timezone: America/New_York
#
# - description: Scheduled Refill 1 AM Job
#   url: /cron/scheduled-refill?schedule=1AM
#   schedule: every day 1:00
#   timezone: America/New_York
#
# - description: Scheduled Refill 1:30 AM Job
#   url: /cron/scheduled-refill?schedule=130AM
#   schedule: every day 1:30
#   timezone: America/New_York
#
# - description: Scheduled Refill 2 AM Job
#   url: /cron/scheduled-refill?schedule=2AM
#   schedule: every day 2:00
#   timezone: America/New_York
#
# - description: Pre CC Charge Job
#   url: /cron/pre-cc-charge
#   schedule: every day 4:00
#   timezone: America/New_York


# description: Scheduled Refill Midnight Job 23:59
@periodic_task(run_every=crontab_with_correct_tz(hour=23, minute=59))
def midnight_job():
    schedule_refill(schedule='MN')


# description: Scheduled Refill 12:01 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=00, minute=01))
def after_midday_job():
    schedule_refill(schedule='1201AM')


# description: Scheduled Refill 1 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=01, minute=00))
def one_hour_day_job():
    schedule_refill(schedule='1AM')


# description: Scheduled Refill 1:30 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=01, minute=30))
def one_hour_with_half_day_job():
    schedule_refill(schedule='130AM')


# description: Scheduled Refill 2 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=02, minute=00))
def two_hour_day_job():
    schedule_refill(schedule='2AM')


# description:  3 AM Job
@periodic_task(run_every=crontab_with_correct_tz(hour=03, minute=00))
def check_refunds_job():
    check_refunds()


# description: 11:59 AM
@periodic_task(run_every=crontab_with_correct_tz(hour=11, minute=59))
def midday_job():
    schedule_refill(schedule='MD')


# description: Prepered Scheduled Refill 14:00 by US/Eastern
@periodic_task(run_every=crontab_with_correct_tz(hour=14, minute=00))
def prerefill_job():
    make_prerefill.delay()


@task
def make_prerefill():
    now = timezone.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    logger.debug('%s, %s, %s', today, tomorrow,  now, )
    autorefills = AutoRefill.objects.filter(enabled=True, renewal_date=tomorrow).exclude(plan__plan_type=Plan.DOMESTIC_TOPUP)
    logger.debug('make_prerefill')
    i = 0
    for autorefill in autorefills:
        logger.debug('make transaction for autorefill %s', autorefill)
        if not autorefill.check_renewal_end_date(today=tomorrow):
            # return self.enabled
            # if False == not enabled then skip autorefill
            continue
        transaction = Transaction.objects.create(user=autorefill.user,
                                                 autorefill=autorefill,
                                                 state="Q",
                                                 company=autorefill.company,
                                                 triggered_by='System')
        logger.debug('run trans %s', transaction)
        queue_prerefill.apply_async(args=[transaction.id], countdown=60*i)
        i += 1


@task
def queue_prerefill(transaction_id):
    PreRefill(transaction_id).main()


@task
def queue_refill(transaction_id):
    Refill(transaction_id).main()


def schedule_refill(schedule):
    # today = timezone.now().date()
    # We stored date in GMT but needs to use in US/Eastern
    today = datetime.now(pytz.timezone('US/Eastern')).date()
    start_date = datetime.combine(today - timedelta(days=1), time(hour=11, minute=59))
    for autorefill in AutoRefill.objects.filter(enabled=False, renewal_date=today, schedule=schedule):
        autorefill.set_renewal_date_to_next(today=today)
    autorefills = AutoRefill.objects.filter(enabled=True, renewal_date=today, schedule=schedule)
    logger.debug('%s Refill^ %s' % (schedule, autorefills))
    for autorefill in autorefills:
        logger.debug('Refill^ %s' % autorefill)
        if Transaction.objects.filter(autorefill=autorefill, started__gt=start_date):
            transaction = Transaction.objects.filter(autorefill=autorefill, started__gt=start_date)[0]
            logger.debug('Found transaction:  %s' % transaction)
        else:
            if not autorefill.check_renewal_end_date(today=today):
                logger.debug('check_renewal_end_date False: today %s' % today)
                continue
            transaction = Transaction.objects.create(user=autorefill.user,
                                                     autorefill=autorefill,
                                                     state="Q",
                                                     company=autorefill.company,
                                                     triggered_by='System')
            logger.debug('Created transaction:  %s' % transaction)
        transaction.autorefill.set_renewal_date_to_next(today=today)
        if transaction.completed:
            logger.debug('transaction completed:  %s' % transaction.completed)
            continue
        transaction.state = Transaction.PROCESS
        transaction.save()
        logger.debug('transaction started:  %s' % transaction)
        queue_refill.delay(transaction.id)


def single_instance_task(timeout):
    def task_exc(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            lock_id = "celery-single-instance-" + func.__name__
            con = get_redis_connection("default")
            with con.lock(lock_id, timeout=timeout):
                ret_value = func(*args, **kwargs)
            return ret_value
        return wrapper
    return task_exc


@single_instance_task(60*10)
def get_unused_pin(plan, company):
    unused_pin = UnusedPin.objects.filter(company=company, plan=plan, used=False)
    if unused_pin:
        unused_pin = unused_pin[0]
        unused_pin.used = True
        unused_pin.save()
        return unused_pin


@task
def check_refunds():
    from ppars.apps.charge.models import Charge
    # check refunds for success
    start_date = datetime.combine(datetime.today() - timedelta(days=2), time.min)
    end_date = datetime.combine(datetime.today(), time.max)
    for cc in Charge.objects.filter(status='R', refunded__range=(start_date, end_date)):
        if not cc.refund_status or cc.refund_status == cc.ERROR or cc.refund_status == cc.PROCESS:
            cc.check_refund()
    # send email
    start_date = datetime.combine(datetime.today() - timedelta(days=3), time.min)
    end_date = datetime.combine(datetime.today() - timedelta(days=3), time.max)
    for cc in Charge.objects.filter(status='R', refunded__range=(start_date, end_date), refund_status='E'):
        failed_check_refunds_customer_notification(cc)


@task
def queue_customer_import(cache_data):
    charge_types = dict()
    send_status_types = dict()
    customers = dict()
    result = list()
    super_profile = UserProfile.objects.filter(superuser_profile=True)
    super_profile = super_profile[0].company
    for send_status_type in Customer.SEND_STATUS_CHOICES:
        send_status_types[send_status_type[1]] = send_status_type[0]
    for charge_type_type in Customer.CHARGE_TYPE_CHOICES:
        charge_types[charge_type_type[1]] = charge_type_type[0]
    user = cache_data['user']
    for customer in Customer.objects.filter(company=user.profile.company):
        customers['%s' % customer] = customer
    for customer in cache_data['customers']:
        try:
            customer['user'] = user
            customer['company'] = user.profile.company
            customer['middle_name'] = customer['middle_name'] or ''
            customer['sc_account'] = customer['sellercloud_account_id']
            del customer['sellercloud_account_id']
            customer['creditcard'] = customer['card_number']
            del customer['card_number']
            customer['send_status'] = send_status_types[customer['send_status']]
            customer['charge_type'] = charge_types[customer['charge_type']]
            customer['enabled'] = (customer['enabled'] and customer['enabled'].upper() == 'TRUE')
            customer['email_success'] = (customer['email_success'] and customer['email_success'].upper() == 'TRUE')
            if customers.get(" ".join([customer['first_name'] or '', customer['middle_name']or '', customer['last_name'] or ''])):
                this_customer = customers.get(" ".join([customer['first_name'], customer['middle_name'], customer['last_name']]))
                for prop in customer:
                    setattr(this_customer, prop, customer[prop])
            else:
                this_customer = Customer(**customer)
            this_customer.save()
            customer['import_status'] = 'Success'
        except Exception, msg:
            customer['import_status'] = 'Error %s' % msg
        finally:
            result.append(customer)
    email_subject = "[EZ-Cloud Autorefill] Status of Customer Import"
    email_body = '''The status of customer import is as follow <br/><br/>
                <table>
                    <thead>
                        <tr>
                            <th>First Name</th>
                            <th>Middle Name</th>
                            <th>Last Name</th>
                            <th>Primary Email</th>
                            <th>Phone Numbers</th>
                            <th>Address</th>
                            <th>City</th>
                            <th>State</th>
                            <th>Zip</th>
                            <th>Charge Type</th>
                            <th>Send Status</th>
                            <th>Email On Success</th>
                            <th>Enabled</th>
                        </tr>
                    </thead>
                <tbody>'''
    for r in result:
        email_body = email_body + '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'%(r['first_name'], r['middle_name'], r['last_name'], r['primary_email'], r['phone_numbers'], r['address'], r['city'], r['state'], r['zip'], r['charge_type'], r['send_status'], r['email_success'], r['enabled'], r['import_status'])
    emailBody = email_body + '</tbody></table><br/><br/>'
    ext_lib.mandrill_emailsend(super_profile.mandrill_key, emailBody, email_subject, super_profile.mandrill_email, user.get_company_profile().email_id)


@task
def queue_autorefill_import(cache_data):
    schedule_types = dict()
    customers = dict()
    result = list()
    # super_profile = UserProfile.objects.filter(superuser_profile=True)
    # super_profile = super_profile[0].company
    for schedule_type in AutoRefill.SCHEDULE_TYPE_CHOICES:
        schedule_types[schedule_type[1]] = schedule_type[0]
    user = cache_data['user']
    # for customer in Customer.objects.filter(company=user.profile.company):
    #     customers['%s' % customer] = customer
    for autorefill in cache_data['autorefills']:
        if 'S' == autorefill.pop('status'):
            autorefill.pop('result')
            logger.debug('import autorefill %s' % autorefill)
            try:
                autorefill['user'] = user
                autorefill['company'] = user.profile.company
                autorefill['trigger'] = 'SC'
                autorefill['refill_type'] = 'FR'
                autorefill['schedule'] = schedule_types[autorefill['schedule']]
                autorefill['pre_refill_sms'] = False
                if autorefill['enabled'].upper() == 'TRUE':
                    autorefill['enabled'] = True
                else:
                    autorefill['enabled'] = False
                AutoRefill.objects.create(**autorefill)
                autorefill['import_status'] = 'Success'
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
                autorefill['import_status'] = 'Error %s' % e
            finally:
                result.append(autorefill)
    subject = "[EZ-Cloud Autorefill] Status of Scheduled Refill Import"
    body = '''The status of scheduled refill imports is as follow <br/><br/>
            <table>
                <thead>
                    <tr>
                        <th>Customer</th>
                        <th>Phone Number</th>
                        <th>Plan</th>
                        <th>Renewal Date</th>
                        <th>Renewal End Date</th>
                        <th>Renewal Interval</th>
                        <th>Schedule</th>
                        <th>Notes</th>
                        <th>Enabled</th>
                        <th>Import Status</th>
                    </tr>
                </thead>
            <tbody>'''
    for r in result:
        body = '''%s<tr>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                        <td>%s</td>
                    </tr>''' % \
               (body,
                r['customer'],
                r['phone_number'],
                r['plan'],
                r['renewal_date'],
                r['renewal_end_date'],
                r['renewal_interval'],
                r['schedule'],
                r['notes'],
                r['enabled'],
                r['import_status'])
    body = body + '</tbody></table><br/><br/>'
    notification = Notification.objects.create(
        company=CompanyProfile.objects.get(superuser_profile=True),
        email=user.get_company_profile().email_id,
        subject=subject,
        body=body,
        send_with=Notification.MAIL
        )
    notification.send_notification()


@task
def queue_import_customers_from_usaepay(company_id, user_id):
    if not settings.TEST_MODE:
        from ppars.apps.price.models import SellingPriceLevel
        user = User.objects.get(id=user_id)
        company = CompanyProfile.objects.get(id=company_id)
        level_price = SellingPriceLevel.objects.get(level='1')
        message = ''
        added = 0
        exists = 0
        not_added = 0
        found = 0
        try:
            if company.usaepay_username and company.usaepay_password:
                payload = {'username': company.usaepay_username, 'password': company.usaepay_password, 'stamp': '4159eb01db85b605b2616f384f903f84a477b1de'}
                s = requests.Session()

                r = s.post('https://secure.usaepay.com/login', data=payload)
                if r.url not in ['https://secure.usaepay.com/console/']:
                    raise Exception("Failed to login to USAePay, please check credentials")
                r = s.get('https://secure.usaepay.com/console/billing?limitstart=0&limit=2000&filter=&sortkey=&sortdir=&level=&type=')

                # r = s.post('https://sandbox.usaepay.com/login', data=payload)
                # if r.url not in ['https://sandbox.usaepay.com/console/']:
                #     raise Exception("Failed to login to USAePay, please check credentials")
                # r = s.get('https://sandbox.usaepay.com/console/billing?limitstart=0&limit=2000&filter=&sortkey=&sortdir=&level=&type=')

                soup2 = BeautifulSoup(r.text)
                forms = soup2.findAll('form')
                usaepay_customers = []
                for form in forms:
                    if form.get('name') == 'custs':
                        inputs = soup2.findAll('input')
                        for obj in inputs:
                            if obj.get('name') == 'sel[]':
                                usaepay_customers.append(obj.get('value'))
                        # for a in form.findAll('a'):
                        #     if 'javascript:editCustomer' in a.get('href'):
                        #         usaepay_customers.append(int(a.get('href').replace('javascript:editCustomer(\'', '').replace('\')', '')))
                found = len(usaepay_customers)
                system_customers = Customer.objects.filter(company=company)
                for customer in system_customers:
                    if customer.usaepay_customer_id:
                        if customer.usaepay_customer_id in usaepay_customers:
                            usaepay_customers.remove(customer.usaepay_customer_id)
                exists = found - len(usaepay_customers)
                if exists + Customer.objects.filter(company=company).count() > company.customer_limit and company.customer_limit != 0:
                    raise Exception('Customer limit has been reached. Please contact administrator.')
                if usaepay_customers:
                    for usaepay_customer in usaepay_customers:
                        if company.usaepay_source_key and company.usaepay_pin:
                            try:
                                token = company.usaepay_authorization()
                                client = SoapClient(wsdl=settings.USAEPAY_WSDL,
                                                    trace=False,
                                                    ns=False)
                                response = client.getCustomer(CustNum=usaepay_customer, Token=token)
                                result = response['getCustomerReturn']
                                if result:
                                    first_name = ''
                                    last_name = ''
                                    enabled = False
                                    city = ''
                                    zip = ''
                                    state = ''
                                    address = ''
                                    primary_email = ''
                                    creditcard = ''
                                    usaepay_custid = ''
                                    company_name = ''
                                    pns = []
                                    logger.debug('Notes "%s"' % result['Notes'])
                                    if 'Notes' in result and result['Notes']:
                                        pns = extract_phone_numbers_from_notes(result['Notes'])
                                    if 'Enabled' in result and result['Enabled']:
                                        enabled = result['Enabled']
                                    if 'BillingAddress' in result:
                                        if 'City' in result['BillingAddress'] and result['BillingAddress']['City']:
                                            city = result['BillingAddress']['City'].strip()
                                        if 'Zip' in result['BillingAddress'] and result['BillingAddress']['Zip']:
                                            zip = result['BillingAddress']['Zip'].strip()
                                        if 'FirstName' in result['BillingAddress'] and result['BillingAddress']['FirstName']:
                                            first_name = result['BillingAddress']['FirstName'].strip()
                                        if 'LastName' in result['BillingAddress'] and result['BillingAddress']['LastName']:
                                            last_name = result['BillingAddress']['LastName'].strip()
                                        if 'Company' in result['BillingAddress'] and result['BillingAddress']['Company']:
                                            company_name = result['BillingAddress']['Company'].strip()
                                        logger.debug('Phone "%s"' % result['BillingAddress']['Phone'])
                                        if 'Phone' in result['BillingAddress'] and result['BillingAddress']['Phone']:
                                            for n in extract_phone_numbers_from_notes(result['BillingAddress']['Phone']):#.strip().replace('-', '').replace(' ', '')
                                                if n not in pns:
                                                    pns.append(n)
                                        if 'State' in result['BillingAddress'] and result['BillingAddress']['State']:
                                            state = result['BillingAddress']['State'].strip()
                                        if 'Street' in result['BillingAddress'] and result['BillingAddress']['Street']:
                                            address = result['BillingAddress']['Street'].strip()
                                        if 'Email' in result['BillingAddress'] and result['BillingAddress']['Email']:
                                            primary_email = result['BillingAddress']['Email'].strip()
                                    if 'PaymentMethods' in result:
                                        if len(result['PaymentMethods']) > 0:
                                            if 'item' in result['PaymentMethods'][0]:
                                                item = result['PaymentMethods'][0]['item']
                                                creditcard = str(item.CardNumber)
                                    if 'CustomerID' in result and result['CustomerID']:
                                        p = result['CustomerID']
                                        for token in [', ', '. ', ' ', ',', '.']:
                                            p = p.replace(token, '|')
                                        p = p.replace('|', ', ').strip()
                                        if ',' != p[-1:]:
                                            p = '%s,' % p
                                        usaepay_custid = p
                                    new_customer = Customer.objects.create(usaepay_customer_id=usaepay_customer,
                                                                           usaepay_custid=usaepay_custid,
                                                                           user=user,
                                                                           company=company,
                                                                           charge_getaway='U',
                                                                           charge_type='CC',
                                                                           save_to_usaepay=True,
                                                                           creditcard=creditcard,
                                                                           first_name=first_name or company_name or str(usaepay_customer),
                                                                           last_name=last_name or str(usaepay_customer),
                                                                           enabled=enabled,
                                                                           city=city,
                                                                           zip=zip,
                                                                           selling_price_level=level_price,
                                                                           state=state,
                                                                           address=address,
                                                                           primary_email=primary_email,
                                                                           )
                                    for number in pns:
                                        PhoneNumber.create(company=company, customer=new_customer, number=number)
                                    added = added + 1
                                    message = '<a href="%s">%s</a><br/>%s' % (reverse('customer_update', args=[new_customer.id]), new_customer, message)
                                if message:
                                    message = 'This is customer from USAePay. Please check them out.  %s' % (message)
                                    # messages.add_message(request, messages.SUCCESS, '%s' % message)
                            except Exception, e:
                                not_added = not_added + 1
                                message = 'Customer with ID "%s" did`t added. Error:%s<br/>%s' % (usaepay_customer, e, message)
                        else:
                            message = 'no USAePay tokens for API requests'
                            # messages.add_message(request, messages.ERROR, '%s' % message)
                else:
                    message = 'All USAePay users already exist in your system'
            else:
                message = 'no USAePay username/password for API requests'
                # messages.add_message(request, messages.ERROR, '%s' % message)
            message = ('%s customers added, %s customers exists, %s not added of %s<br/><br/>%s' %
                   (added, exists, not_added, found, message)
                   )
        except Exception, e:
            message = '%s<br/><br/>%s' % (message, e)
        finally:
            ImportLog.objects.create(
                company=company,
                command='USAePay for user %s' % company,
                message=message,
                )


def extract_phone_numbers_from_notes(s):
    pn = ''
    pns = []
    part = ''
    result = s.strip().replace('-', '')
    for token in s.replace('-', ''):
        if token in (' ', '\n', '\t'):
            if len(part) > 10:
                result = result.replace(part, '')
            part = ''
        else:
            part = '%s%s' % (part, token)
    for token in result.replace(' ', ''):
        if token.isdigit():
            pn = '%s%s' % (pn, token)
        else:
            if pn and 10 == len(pn) and pn not in pns:
                pns.append(pn)
            pn = ''
    if pn and 10 == len(pn) and pn not in pns:
        pns.append(pn)
    return pns


@task
def queue_import_phone_numbers(company, rows):
    if not settings.TEST_MODE:
        message = ''
        added = 0
        exists = 0
        not_added = 0
        for row in rows:
            try:
                phone_number = int(row[3])
                customers = Customer.objects.filter(company=company, usaepay_custid__contains='%s,' % row[0])
                if not customers.exists():
                    message = '%s<br/>CustID "%s" don`t associate for any customer.' % (message, row[0])
                    not_added += 1
                    continue
                if customers.count() > 1:
                    customer_list = []
                    for customer in customers:
                        customer_list.append('<a href="%s">%s</a>' % (reverse('customer_update', args=[customer.id]), customer))
                    message = '%s<br/>%s associate for CustID "%s". Phone number don`t added. Please, change CustId and try again' % (message, ', '.join(customer_list), row[0])
                    not_added += 1
                    continue
                customer = customers[0]
                phonenumbers = PhoneNumber.objects.filter(company=company, number=str(phone_number))
                note = []
                if phonenumbers:
                    for number in phonenumbers:
                        note.append('<a href="%s">%s</a>' % (reverse('customer_update', args=[number.customer.id]), number.customer))
                    cust_ex = ', '.join(note)
                    message = '%s<br/>Number "%s" can not be added to <a href="%s">%s</a>. It is already exists for %s' % (message, phone_number, reverse('customer_update', args=[customer.id]), customer, cust_ex)
                    exists += 1
                    continue
                if str(phone_number) not in phonenumbers.values_list('number', flat=True): #TODO: test
                    PhoneNumber.objects.create(customer=customer, company=company, number=phone_number)
                    message = '%s<br/>Number "%s" was added to customer <a href="%s">%s</a>' % (message, phone_number, reverse('customer_update', args=[customer.id]), customer)
                    added += 1
                else:
                    message = '%s<br/>Number "%s" already exists for <a href="%s">%s</a>' % (message, phone_number, reverse('customer_update', args=[customer.id]), customer)
                    exists += 1
            except Exception, e:
                message = '%s<br/><br/>%s<br/>' % (message, e)
                not_added += 1
        message = ('%s numbers added, %s numbers exists, %s not added of %s<br/><br/>%s' %
                   (added, exists, not_added, len(rows), message)
                   )
        ImportLog.objects.create(
            company=company,
            command='Phone numbers for %s' % company,
            message=message,
            )


@task
def queue_compare_pins_with_dollarphone(company_id):
    if not settings.TEST_MODE:
        from ppars.apps.charge.models import Charge
        report = 'Problem to login to Dollar Phone. Check company settings.'
        url = 'https://www.dollarphonepinless.com/sign-in'
        end = '{d.month}/{d.day}/{d.year}/'.format(d=datetime.now())
        s = requests.Session()
        company = CompanyProfile.objects.get(id=company_id)
        status = PinReport.SUCCESS
        if PinReport.objects.filter(company=company):
            start = PinReport.objects.filter(company=company, status=PinReport.SUCCESS).order_by('created').last().created.date().strftime("%m/%d/%Y")
        else:
            start = '01/01/2010'
        try:
            r = s.get(url, auth=HTTPBasicAuth(company.dollar_user, company.dollar_pass))
            soup2 = BeautifulSoup(r.text)
            options = soup2.findAll('option')
            statements_url = ''
            for option in options:
                if option.text == 'Statements':
                    statements_url = option.get('value')
                    break
            if statements_url:
                s.get(statements_url)
                s.get('https://reports.dollarphonepinless.com/statements/?sid=%s' % statements_url.split('sid=')[1])
                s.get('https://reports.dollarphonepinless.com/statements/tree.aspx')
                r = s.get('https://reports.dollarphonepinless.com/statements/nodes.ashx?id=')
                soup = BeautifulSoup(r.text)
                node = soup.find('li', 'jstree-open').get('id')
                r = s.get('https://reports.dollarphonepinless.com/statements/transactions.aspx?id=%s&from=%s&to=%s' % (node, start, end))
                soup = BeautifulSoup(r.text)
                if soup.find('h2'):
                    if soup.find('h2').text == '&nbsp; Too Many Records':
                        r = s.get('https://reports.dollarphonepinless.com/statements/transactions.aspx?id=%s&from=%s&to=%s&force=1' % (node, start, end))
                        soup = BeautifulSoup(r.text)
                try:
                    table = soup.find("table", attrs={"id": "treewindowtable"})
                    table_body = table.find('tbody')
                    rows = table_body.findAll('tr')
                    data = []
                    for row in rows:
                        try:
                            cols = row.findAll('td')
                            cols = [ele.text.strip() for ele in cols]
                            if 'rtr' in cols[3].lower():
                                continue
                            if UnusedPin.objects.filter(pin=cols[2], company=company) or \
                                    Transaction.objects.filter(pin=cols[2], company=company) or \
                                    Charge.objects.filter(pin=cols[2], company=company):
                                continue
                            pin_message = 'pin "%s" for plan "%s"[%s]' % (cols[2], cols[3], cols[5])
                        except Exception, e:
                            pin_message = '%s' % e
                        data.append(pin_message)
                    report = '<br/>'.join(data)
                except Exception, e:
                    logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
                    if soup.find('div', id='core').find('h2').text == 'No Records Found':
                        report = 'No Records Found. %s' % soup.find('div', id='core').find('p').text
        except Exception, e:
                report = e
                status = PinReport.ERROR
        finally:
            report = '<strong>Pin report from %s to %s</strong><br/>%s' % (start, end, report)
            PinReport.objects.create(company=company, report=report, status=status)