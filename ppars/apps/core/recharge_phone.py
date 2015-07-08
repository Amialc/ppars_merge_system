import cookielib
import json
import logging
import re
import tempfile
import traceback
import time
from datetime import datetime, timedelta
import urllib
from BeautifulSoup import BeautifulSoup
from dateutil.relativedelta import relativedelta
import decimal
from django.core.cache import cache
from django.conf import settings
from django.core.urlresolvers import reverse
from django.template.defaultfilters import slugify
import mechanize
from pytz import timezone
import requests
from twilio.rest import TwilioRestClient
from deathbycaptcha.deathbycaptcha import SocketClient
from ppars.apps.core import ext_lib
from ppars.apps.core.dollarphone import dpapi_topup
from ppars.apps.core.dollarphone import dpsite_topup
from ppars.apps.core.models import Transaction, UserProfile, CarrierAdmin, \
    CaptchaLogs, CommandLog, CompanyProfile, Plan, Customer, TransactionStep
from ppars.apps.core.send_notifications import SendNotifications

logger = logging.getLogger('ppars')


class RechargePhone:

    def __init__(self, id):
        self.transaction = Transaction.objects.get(id=id)
        self.company = UserProfile.objects.get(user=self.transaction.user).company
        self.super_company = CompanyProfile.objects.get(superuser_profile=True)
        self.customer = self.transaction.customer
        self.carrier = self.transaction.autorefill.plan.carrier
        self.transaction.current_step = 'recharge_phone'

    def main(self):
        try:
            if self.transaction.autorefill.plan.plan_type == Plan.DOMESTIC_TOPUP:
                if self.customer.charge_getaway == Customer.DOLLARPHONE:
                    from ppars.apps.charge.models import TransactionCharge
                    for transacttion_charges in TransactionCharge.objects.filter(transaction=self.transaction):
                        if not transacttion_charges.charge.atransaction:
                            continue
                        self.transaction.add_transaction_step(
                            'recharge phone',
                            'begin topup',
                            'S',
                            'Phone was refilled, details are at <a target="blank"'
                            ' href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>.' %
                            transacttion_charges.charge.atransaction)
                    return self.transaction
                self.topup()
            else:
                # if plan have type Get Pin - we start manual refill
                # search Dealer Site (with authorization data to Carrier site)
                if self.carrier.admin_site:
                    if CarrierAdmin.objects.filter(company=self.transaction.company, carrier=self.carrier):
                        self.carrieradmin = CarrierAdmin.objects.get(company=self.transaction.company, carrier=self.carrier)
                    else:
                        raise Exception('Dealer Site user details not added for '
                                        'carrier %s, please add it '
                                        '<a href="%s">here<a>.' %
                                        (self.carrier, reverse('carrier_admin_create')))
                self.transaction.add_transaction_step('recharge phone', 'begin', 'S', "Call the carrier's recharge function")
                if not settings.TEST_MODE:
                    getattr(self, slugify(self.carrier).replace('-', '_'))()
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            self.transaction.add_transaction_step('recharge phone', 'failed', 'E', '%s' % e)
            raise Exception(e)
        finally:
            self.transaction.save()
        return self.transaction

    def topup(self):
        if settings.TEST_MODE:
            self.transaction.add_transaction_step('recharge phone', 'begin topup', 'S', '')
            self.transaction.add_transaction_step('recharge phone', 'api_begin', 'S', 'Initializing the dollarphone API client in TEST mode')
            self.transaction.add_transaction_step('recharge phone', 'end topup', 'S', u'%s' % "Test Mode on")
            return False

        self.transaction.add_transaction_step('recharge phone', 'begin', 'S', '')
        plan = self.transaction.autorefill.plan

        if self.company.dollar_type == 'A':
            self.transaction.add_transaction_step('recharge phone', 'api_begin', 'S', 'Initializing the dollarphone API client')
            if not plan.api_id:
                raise Exception('API Id for this plan has not been updated, please request the admin to update the plan with the API ID')
            form_fields = {
                    'username': self.company.dollar_user,
                    'password': self.company.dollar_pass,
                    'OfferingId': plan.api_id,
                    'Amount': plan.plan_cost,
                    'PhoneNumber': self.transaction.autorefill.phone_number,
                    'Transaction': self.transaction.id,
                    'callbackUrl': self.custom_redirect_url('pparsb_response', self.transaction.id),
            }
            self.transaction.add_transaction_step('recharge phone', 'api_request', 'S', 'Requesting topup from dollarphone API')
            status, adv_status = dpapi_topup(form_fields)

        else:
            self.transaction.add_transaction_step('recharge phone', 'site_begin', 'S', 'Initializing the dollarphone Site client')
            form_fields = {
                    'username': self.company.dollar_user,
                    'password': self.company.dollar_pass,
                    'Carrier': plan.carrier.name,
                    'Plan': plan.plan_name,
                    'Amount': '$%s' % plan.plan_cost,
                    'PhoneNumber': self.transaction.autorefill.phone_number,
                    'Transaction': self.transaction.id,
                    'callbackUrl': self.custom_redirect_url('pparsb_response', self.transaction.id),
            }
            self.transaction.add_transaction_step('recharge phone', 'site_request', 'S', 'Requesting topup from dollarphone Site')
            status, adv_status = dpsite_topup(form_fields)
            if not status:
                raise Exception(adv_status)

        self.transaction.add_transaction_step('recharge phone', 'end', 'S', u'%s' % adv_status)

    def page_plus_cellular(self):
        try:
            if self.transaction.retry_count and self.check_previous_try():
                return
            # Make refill on PagePlus
            if self.company.pageplus_refillmethod == 'CP':
                self.scrape_page_plus_with_deathbycaptcha()
            else:
                self.twilio_top_up("8773596695", "ww1ww%swwwwwwwwwwwwwwwwwwwwwwww1ww1ww1wwwwwwwwwwwwwwwwwwwwwwww%s" % (self.transaction.autorefill.phone_number, self.transaction.pin.replace('-', '')))
            # Check result of refill
            self.transaction.add_transaction_step(
                'recharge phone',
                'scrape_pageplus',
                'S',
                'Scarping pageplus for verification')
            scrape_result = self.page_plus_cellular_scrape()
            self.transaction.add_transaction_step(
                'recharge phone',
                'end',
                'S',
                u'%s' % scrape_result)
        except Exception, e:
            if self.check_previous_try():
                return
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            raise Exception(u'%s' % e)

    def check_previous_try(self):
        self.transaction.add_transaction_step(
            'recharge phone',
            'check previous try',
            'S',
            'Checking if previous try was successful')
        try:
            scrape_result = self.carrier.name
            if 'PAGE PLUS CELLULAR' == self.carrier.name:
                scrape_result = self.page_plus_cellular_scrape()
            elif 'RED POCKET' == self.carrier.name:
                scrape_result = self.red_pocket_scrape()
            self.transaction.add_transaction_step(
                'recharge phone',
                'end',
                'S',
                scrape_result)
            return True
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))

    def scrape_more_information_page_plus(self, page):
        details = ""
        soup = BeautifulSoup(page)
        logger.debug('DETAILS %s' % page)
        plan_balance_details = soup.find("div", id="ContentPlaceHolderDefault_mainContentArea_Item3_AccountStatus_6_divDealerBundleDetails")
        if plan_balance_details:
            plan_balance_details = plan_balance_details.contents
            Talk_n_Text = "%s %s" % (plan_balance_details[0].contents[0].contents[0].string,
                                     plan_balance_details[0].contents[0].contents[1].string)
            min_details = plan_balance_details[2].contents[0].string
            txt_details = plan_balance_details[2].contents[1].string
            data_details = plan_balance_details[2].contents[2].string
            details = "Plan Balance Details:" \
                      "\t%s\nmin:\t%s\ntxt:\t%s\ndata:\t%s" % (Talk_n_Text, min_details, txt_details, data_details)
        return details

    def page_plus_cellular_scrape(self):
        today = datetime.now(timezone('US/Eastern')) + relativedelta(months=1)
        expiry_like_date1 = '(Expiring {d.month}/{d.day}/{d.year})'.format(d=today)
        expiry_like_date2 = '(Expiring {d.month}/{d.day}/{d.year})'.format(d=today - timedelta(1))
        expiry_like_date3 = '(Expiring {d.month}/{d.day}/{d.year})'.format(d=today + timedelta(1))
        expiry_like_date4 = '(Expiring {d.month}/{d.day}/{d.year})'.format(d=today - timedelta(2))
        expiry_like_date5 = '(Expiring {d.month}/{d.day}/{d.year})'.format(d=today + timedelta(2))
        normPin = self.transaction.pin.replace('-', '')
        br = self.login_site(
            'https://www.pagepluscellular.com/login',
            'https://dealer.pagepluscellular.com/my-profile/account-summary.aspx',
            self.carrieradmin.username,
            'username',
            self.carrieradmin.password,
            'password'
        )
        br.follow_link(br.links(text='MDN/Number Status').next())
        br.select_form(nr=0)
        br.form['ctl00$ctl00$ctl00$ContentPlaceHolderDefault$mainContentArea$Item3$AccountStatus_6$txtPhone'] = str(self.transaction.autorefill.phone_number)
        br.submit()
        soup = BeautifulSoup(br.response().read())
        if soup.find("div", id="ContentPlaceHolderDefault_mainContentArea_Item3_AccountStatus_6_divResult"):
            stacked_pin = soup.find("div", id="ContentPlaceHolderDefault_mainContentArea_Item3_AccountStatus_6_divStackedCardsDetails").contents[2].contents[0].string
            expiry_date = soup.find("div", id="ContentPlaceHolderDefault_mainContentArea_Item3_AccountStatus_6_divDealerBundleDetails").contents
            details = self.scrape_more_information_page_plus(br.response().read())
            if expiry_date:
                expiry_date = expiry_date[0].contents[0].contents[1].strip()
            else:
                expiry_date = None
            balance = soup.find("span", id="ContentPlaceHolderDefault_mainContentArea_Item3_AccountStatus_6_lblBalance").string
            if stacked_pin and stacked_pin == '%s********%s' % (normPin[:2], normPin[10:]):
                return "PagePlus Recharge successful, scraped stacked pin %s \n%s" % (stacked_pin, details)
            elif expiry_date in [expiry_like_date1, expiry_like_date2, expiry_like_date3, expiry_like_date4, expiry_like_date5]:
                return "PagePlus Recharge successful, scraped %s \n%s" % (expiry_date, details)
            elif decimal.Decimal(balance.replace('$', '')) >= self.transaction.autorefill.plan.plan_cost:
                return "PagePlus Recharge successful, scraped balance %s\n%s" % (balance, details)
        raise Exception("PagePlus Recharge Failed, Scape result inconclusive")

    def red_pocket_scrape(self):
        s, base_url = self.login_site_request('https://my.redpocketmobile.com/index/checkLogin',
                                              'https://my.redpocketmobile.com/sdealer',
                                              self.carrieradmin.username,
                                              'username',
                                              self.carrieradmin.password,
                                              'password')
        r = s.post('%s/search/' % base_url, data={'search': '9175864186'})
        account_id = r.url.split("/").pop()
        r = s.post('%s/accounts/ajax-get-orders-data/account_id/%s' % (base_url, account_id),
                   data={"_search": False, "sidx": "date", "sord": "desc"})
        response_json = json.loads(r.text)
        for row in response_json['rows']:
            if row['id'] == self.transaction.pin:
                return 'Previous try successful, pin %s found in latest red ' \
                       'pocket order' % self.transaction.pin
        raise Exception('Red Pocket Recharge Failed, Scape result inconclusive')

    def scrape_page_plus_with_deathbycaptcha(self):
        if (self.transaction.retry_count == self.company.short_retry_limit + 1 and
                        'used' not in self.transaction.adv_status):
            self.twilio_top_up("8773596695", "ww1ww%swwwwwwwwwwwwwwwwwwwwwwww1ww1ww1wwwwwwwwwwwwwwwwwwwwwwww%s" % (self.transaction.autorefill.phone_number, self.transaction.pin.replace('-', '')))
            return
        self.transaction.add_transaction_step('recharge phone', 'login_pp', 'S', 'Log in to pageplus dealer site')
        logger.error("Login to pp with login '%s' and pass '%s'" % (self.carrieradmin.username, self.carrieradmin.password))
        cache.set(key="%s_pp" % self.transaction.id, value=True, timeout=600)
        br = self.login_site(
                'https://www.pagepluscellular.com/login',
                'https://dealer.pagepluscellular.com/my-profile/account-summary.aspx',
                self.carrieradmin.username,
                'username',
                self.carrieradmin.password,
                'password'
        )
        self.transaction.add_transaction_step('recharge phone', 'setup_request', 'S', 'Set up the recharge request')
        # modified phone number to format (xxx) xxx-xxxx
        mod_ph = '(%s) %s-%s' % (str(self.transaction.autorefill.phone_number)[:3], str(self.transaction.autorefill.phone_number)[3:6], str(self.transaction.autorefill.phone_number)[6:])
        br.follow_link(br.links(text='Replenish').next())
        refill_form = BeautifulSoup(br.response().read())
        cresponse = br.open_novisit(refill_form.find('iframe')['src'].replace('noscript', 'challenge'))
        challenge = cresponse.get_data().split(':')[1].split(',')[0].strip().replace("'", "")
        key = cresponse.get_data().split(':')[6].split(',')[0].strip().replace("'", "")
        rParams = {
                'k': key,
                'c': challenge,
                'reason': 'i',
                'type': 'image',
                'lang': 'en',
        }
        rresponse = br.open_novisit('https://www.google.com/recaptcha/api/reload?%s' % urllib.urlencode(rParams))
        logger.debug('first url: https://www.google.com/recaptcha/api/reload?%s' % urllib.urlencode(rParams))
        newC = rresponse.read().split("'")[1]
        rParams['c'] = newC
        google_image = 'https://www.google.com/recaptcha/api/image?%s' % urllib.urlencode(rParams)
        iresponse = br.open_novisit(google_image)
        logger.debug('second url: %s' % google_image)
        temp_file = tempfile.TemporaryFile()
        temp_file.write(iresponse.read())
        temp_file.seek(0)
        # Initializing death captcha client
        client = SocketClient(self.super_company.deathbycaptcha_user, self.super_company.deathbycaptcha_pass)
        deathbycaptcha_balance = client.get_balance()
        if deathbycaptcha_balance >= self.super_company.deathbycaptcha_email_balance:
            self.super_company.deathbycaptcha_emailed = True
            self.super_company.save()
        captcha = client.decode(temp_file)
        if captcha:
            logger.debug('captcha %s ' % captcha)
            # count captcha and log it
            if not self.super_company.deathbycaptcha_current_count:
                self.super_company.deathbycaptcha_current_count = 0
            self.super_company.deathbycaptcha_current_count += 1
            self.super_company.save()
            CaptchaLogs.objects.create(
                user=self.transaction.user,
                user_name=self.transaction.user.username,
                customer=self.transaction.autorefill.customer,
                customer_name=self.transaction.autorefill.customer,
                carrier=self.transaction.autorefill.plan.carrier,
                carrier_name=self.transaction.autorefill.plan.carrier.name,
                plan=self.transaction.autorefill.plan,
                plan_name=self.transaction.autorefill.plan.sc_sku,
                refill_type=self.transaction.autorefill.get_refill_type_display(),
                transaction=self.transaction,
            )
            br.select_form(nr=0)
            br.form.set_all_readonly(False)
            br.form['ctl00$ctl00$ctl00$ContentPlaceHolderDefault$mainContentArea$Item3$AddMinutes_6$WizardReplenishMinutes$ucPhoneNumber'] = mod_ph
            br.form['ctl00$ctl00$ctl00$ContentPlaceHolderDefault$mainContentArea$Item3$AddMinutes_6$WizardReplenishMinutes$txtPIN'] = self.transaction.pin.replace('-','')
            br.form['recaptcha_response_field'] = captcha["text"]
            br.form['recaptcha_challenge_field'] = newC
            self.transaction.add_transaction_step('recharge phone', 'scrape captcha', 'S', 'Captcha <a href="%s">image</a>resolved with answer "%s"' % (google_image, captcha["text"]))
            self.transaction.add_transaction_step('recharge phone', 'refill_request', 'S', 'Request recharge from pageplus')
            br.submit()
            refill_response = BeautifulSoup(br.response().read())
            more_details = self.scrape_more_information_page_plus(br.response().read())
            if refill_response.find("div", id="ContentPlaceHolderDefault_mainContentArea_Item3_AddMinutes_6_WizardReplenishMinutes_divResult"):
                outcome = refill_response.find("span", id="ContentPlaceHolderDefault_mainContentArea_Item3_AddMinutes_6_WizardReplenishMinutes_lblOutcome").string
                notes = refill_response.find("span", id="ContentPlaceHolderDefault_mainContentArea_Item3_AddMinutes_6_WizardReplenishMinutes_lblNotes").string
                if outcome.upper() == 'SUCCESSFUL':
                    scrape_result = 'Pageplus refill successful with message %s %s' % (notes, more_details)
                else:
                    CommandLog.objects.create(command='pageplus1', message='%s\n%s' % (self.transaction.get_full_url(), refill_response))
                    raise Exception('Pageplus refill failed with error %s %s' % (notes, more_details))
            else:
                message = refill_response.find("span", id="ContentPlaceHolderDefault_mainContentArea_Item3_AddMinutes_6_WizardReplenishMinutes_lblMessage").string
                if not message:
                    cdata = refill_response.findAll(text=re.compile("CDATA"))
                    for msg in cdata:
                        if "" in msg:
                            message = msg.split(",")[1].split(")")[0]
                    #logger.info('%s'%refill_response.prettify())
                    #message = refill_response.find("div", id="qtip-0-content").string
                CommandLog.objects.create(command='pageplus2', message='%s\n%s' % (self.transaction.get_full_url(), refill_response))
                raise Exception('Pageplus refill failed with error %s %s' % (message, more_details))
            # send email to admin when we had low balance
            # 1 captcha cost 0,139 cent
            if (self.super_company.deathbycaptcha_email_balance
                    and deathbycaptcha_balance <= self.super_company.deathbycaptcha_email_balance
                    and self.super_company.deathbycaptcha_emailed):
                email_subject = "[EZ-Cloud Autorefill]Low balance on " \
                                " <a href=\"http://www.deathbycaptcha.com/\">DeathByCaptcha</a>"
                email_body = "Hi Admin,<br/><br/>You have only %s $ balance on DeathByCaptcha. " \
                             "Please, chardge you " \
                             "<a href=\"http://www.deathbycaptcha.com/user/order\">balance</a>." \
                             "<br/></br>Regards,</br>EZ-Cloud Autorefill System" % (
                            deathbycaptcha_balance,
                )
                ext_lib.mandrill_emailsend(self.super_company.mandrill_key, email_body, email_subject, self.super_company.mandrill_email, self.super_company.email_id)
                self.super_company.deathbycaptcha_emailed = False
                self.super_company.save()
            # send email to admin after some number of captcha what he needs
            if (self.super_company.deathbycaptcha_count
                    and self.super_company.deathbycaptcha_count <= self.super_company.deathbycaptcha_current_count):
                self.super_company.deathbycaptcha_current_count = 0
                self.super_company.save()
                email_subject = "[EZ-Cloud Autorefill]Report of %s used captha's" % (self.super_company.deathbycaptcha_count)
                start = CaptchaLogs.objects.count() - self.super_company.deathbycaptcha_count
                logs = CaptchaLogs.objects.all().order_by('created')[start:]
                body = [log.get_string() for log in logs]
                email_body ='<br/>'.join(body)
                ext_lib.mandrill_emailsend(self.super_company.mandrill_key, email_body, email_subject, self.super_company.mandrill_email, self.super_company.email_id)
        else:
                raise Exception('Failed to get response from death by captcha')

    def twilio_top_up(self, dealer_phone_number, voice):
        if not self.company.twilio_sid or \
                not self.company.twilio_auth_token or \
                not self.company.twilio_number:
            raise Exception('Twilio account is missing in company')

        self.transaction.add_transaction_step(
            'recharge phone',
            'voice refill',
            'S',
            'Calling to refill phone')
        cache.set(key="%s_pp" % self.transaction.id, value=True, timeout=800)
        client = TwilioRestClient(self.company.twilio_sid, self.company.twilio_auth_token)
        call = client.calls.create(
            url=self.custom_redirect_url('twilio_request'),
            method="GET",
            from_="+1%s" % self.company.twilio_number,
            to="+1%s" % dealer_phone_number,
            send_digits=voice
        )
        self.transaction.add_transaction_step(
            'recharge phone',
            'call_wait',
            'S',
            'Call was started, wait for complete')
        cache.set(key=call.sid, value="", timeout=800)
        time.sleep(60)
        wait_count = 1
        while not cache.get(call.sid):
            if wait_count > 24:
                raise Exception('No response received from twilio')
            time.sleep(10)
            wait_count = wait_count + 1
        self.transaction.add_transaction_step(
            'recharge phone',
            'call_complete',
            'S',
            'Call completed the recording is available at '
            '<a target="blank" href="%s">link</a>.' % cache.get(call.sid))

    def login_site(self, url, url_success, user, user_tag, passw, passw_tag):
        br = mechanize.Browser()
        cj = cookielib.LWPCookieJar()
        br.set_handle_equiv(True)
        br.set_handle_redirect(True)
        br.set_handle_referer(True)
        br.set_handle_robots(False)
        br.set_handle_refresh(mechanize._http.HTTPRefreshProcessor(), max_time=1)
        br.addheaders = [('User-agent', 'Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.1) Gecko/2008071615 Fedora/3.0.1-1.fc9 Firefox/3.0.1')]
        br.open(url)
        br.select_form(nr=0)
        br.form[user_tag] = user
        br.form[passw_tag] = passw
        br.submit()
        br.open(url_success)
        if br.geturl() not in url_success:
            raise Exception("Failed to login to %s, please check the credentials"%url)
        return br

    def login_site_request(self, url, url_success, user, user_tag, passw, passw_tag):
        payload = {user_tag: user, passw_tag: passw}
        s = requests.Session()
        s.post(url, data=payload)
        r = s.get(url_success)
        if r.url not in url_success:
            raise Exception("Failed to login to %s, please check the credentials" % url)
        base_url = r.url
        return s, base_url

    def red_pocket(self):
        try:
            if self.transaction.retry_count and self.check_previous_try():
                return
            self.red_pocket_standart_refill()
        except Exception, e:
            self.transaction.add_transaction_step(
                'recharge phone',
                'red pocket',
                TransactionStep.ERROR,
                'Red Pocket failed')
            if self.check_previous_try():
                return
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            raise Exception(u'Red Pocket failed with error: %s' % e)

    def red_pocket_unlogin_refill(self):
        r = requests.get('http://goredpocket.com/ajax/apply_refill2.php?mdn=%s&voucher=%s' %
                         (self.transaction.autorefill.phone_number, self.transaction.pin))
        response = json.loads(r.text)
        if 'error' in response:
            raise Exception(response['text'])
        else:
            self.transaction.add_transaction_step(
                'recharge phone',
                'RedPocket',
                'S',
                'Red Pocket recharge succeeded. %s' % (response['text']))

    def red_pocket_voice_refill(self):
        self.twilio_top_up("8889933888", "wwwwwwww1www2wwwwwww%swwwwwwwwwwwwwwwwwwwwwwww%s" % (self.transaction.autorefill.phone_number, self.transaction.pin.replace('-', '')))

    def red_pocket_standart_refill(self):
        if (self.transaction.retry_count == self.company.short_retry_limit + 1 and
                        'used' not in self.transaction.adv_status):
                self.red_pocket_unlogin_refill()
        else:
            self.transaction.add_transaction_step(
                'recharge phone',
                'RedPocket',
                'S',
                'Logging into RedPocket website')
            # red pocket login
            s, base_url = self.login_site_request('https://my.redpocketmobile.com/index/checkLogin',
                                                  'https://my.redpocketmobile.com/sdealer',
                                                  self.carrieradmin.username,
                                                  'username',
                                                  self.carrieradmin.password,
                                                  'password')
            # sending request to red poket
            payload = {'mdn': self.transaction.autorefill.phone_number,
                       'voucher': self.transaction.pin,
                       'id': '',
                       'submit': "Apply Voucher",
                       'validate': "1"}
            r = s.post('%s/accounts/apply-voucher-mdn/id/' % base_url, data=payload)
            # extract session from last response
            soup2 = BeautifulSoup(r.text)
            inputs = soup2.findAll('input')
            session = None
            for inp in inputs:
                if inp.get('name') == 'session':
                    session = inp.get('value')
            # if we have session - make request to apply voucher
            if session:
                payload = {'mdn': self.transaction.autorefill.phone_number,
                           'voucher': self.transaction.pin,
                           'id': '',
                           'session': session,
                           'submit': "Apply Voucher",
                           'refill': "1"}
                r = s.post('%s/accounts/apply-voucher-mdn/id/' % base_url, data=payload)
            # parsing response for extract answer
            soup2 = BeautifulSoup(r.text)
            caption = soup2.find('caption')
            trs = soup2.findAll('tr')
            redpocket_plan = ''
            redpocket_balance = ''
            for tr in trs:
                for th in tr.findAll('th'):
                    # search errors in fail refill
                    if th.text == 'Error text:':
                        logger.debug('th %s' % th)
                        for td in tr.findAll('td'):
                            logger.debug('td %s' % td)
                            raise Exception('Red Pocket recharge failed with message, %s' % td.text)
                    # search balance in successfully refill
                    if th.text == 'Balance:':
                        for td in tr.findAll('td'):
                            redpocket_balance = 'Current balance %s' % td.text
                    # search current plan in successfully refill
                    if th.text == 'Current plan:':
                        for td in tr.findAll('td'):
                            redpocket_plan = 'Current plan: %s' % td.text
            # check is refill success
            if caption.text == 'Refill Voucher Done':
                self.transaction.add_transaction_step(
                    'recharge phone',
                    'RedPocket',
                    'S',
                    'Red Pocket recharge succeeded. %s %s' % (redpocket_plan, redpocket_balance))
            else:
                # catch error
                raise Exception('Red Pocket recharge failed with message, "%s"' % caption.text)

    def airvoice(self):
        if settings.TEST_MODE:
            self.transaction.add_transaction_step('recharge phone', 'begin airvoice', 'S', '')
            self.transaction.add_transaction_step('recharge phone', 'end', 'S', 'Previous try successful, scrape is %s' % "Test mode")
            self.transaction.add_transaction_step('recharge phone', 'end airvoice', 'S', u'%s' % "Test Mode on")
            return False
        try:
            self.transaction.add_transaction_step('recharge phone', 'getaccount_airvoice', 'S', 'Get account information from Air Voice')
            br = mechanize.Browser()
            cj = cookielib.LWPCookieJar()
            br.set_handle_equiv(True)
            br.set_handle_redirect(True)
            br.set_handle_referer(True)
            br.set_handle_robots(False)
            br.set_handle_refresh(mechanize._http.HTTPRefreshProcessor(), max_time=1)
            br.addheaders = [('User-agent', 'Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.1) Gecko/2008071615 Fedora/3.0.1-1.fc9 Firefox/3.0.1')]
            br.open('https://www.airvoicewireless.com/PINRefill.aspx')
            br.select_form(nr=0)
            br.form.set_all_readonly(False)
            br.form['ctl00$ContentPlaceHolder1$txtSubscriberNumber'] = str(self.transaction.autorefill.phone_number)
            br.form['ctl00$ContentPlaceHolder1$btnAccountDetails'] = 'View Account Info'
            br.submit()
            account_info = BeautifulSoup(br.response().read())
            subscriber = account_info.find("span", id="ctl00_ContentPlaceHolder1_lblSubscriberNumber").string
            if subscriber == 'n/a':
                    error = account_info.find("span", id="ctl00_ContentPlaceHolder1_lblErrorMessage").string
                    raise Exception("Failed to get account info, error is %s" % error)
            expiry_date = account_info.find("span", id="ctl00_ContentPlaceHolder1_lblairTimeExpirationDate").string
            self.transaction.add_transaction_step('recharge phone', 'recharge_airvoice', 'S', 'Requesting recharge for subscriber %s, with expiry %s' % (subscriber, expiry_date))
            data = urllib.urlencode({
                '__EVENTTARGET': 'ctl00$ContentPlaceHolder1$btnAccountRecharge',
                '__EVENTARGUMENT': '',
                '__VIEWSTATE': account_info.find("input", id="__VIEWSTATE")['value'],
                'ctl00$ContentPlaceHolder1$txtSubscriberNumber': str(self.transaction.autorefill.phone_number),
                'ctl00$ContentPlaceHolder1$txtPin': str(self.transaction.pin),
            })
            response = br.open('https://www.airvoicewireless.com/PINRefill.aspx', data)
            recharge_info = BeautifulSoup(response.read())
            new_expiry_date = recharge_info.find("span", id="ctl00_ContentPlaceHolder1_lblairTimeExpirationDate").string
            message = recharge_info.find("span", id="ctl00_ContentPlaceHolder1_lblErrorMessage").string
            if new_expiry_date != expiry_date or message == 'Refill has been added':
                    self.transaction.add_transaction_step('recharge phone', 'end', 'S', "Phone recharged successfully with new expiry date %s" % new_expiry_date)
                    return
            raise Exception("Failed to recharge phone, error is %s" % message)
        except Exception, msg:
            logger.error("Exception: %s. Trace: %s." % (msg, traceback.format_exc(limit=10)))
            self.transaction.add_transaction_step('recharge phone', 'end', 'E', u'%s' % msg)
            self.transaction.save()
            raise Exception(u'%s' % msg)

    def h2o_unlimited(self):
        if settings.TEST_MODE:
            self.transaction.add_transaction_step('recharge phone', 'login_h20', 'S', 'Logging into h20 website in TEST mode')
            self.transaction.add_transaction_step('recharge phone', 'recharge_h20', 'S', 'Requesting recharge from h20 in TEST mode')
            return False
        try:
            self.transaction.add_transaction_step('recharge phone', 'login_h20', 'S', 'Logging into h20 website')
            # log in to the H2O dealer site
            br = self.login_site('https://www.h2owirelessnow.com/mainControl.php?page=login',
                                 self.transaction.autorefill.plan.carrier.admin_site,
                                 self.carrieradmin.username,
                                 'email',
                                 self.carrieradmin.password,
                                 'pass'
                                 )
            # install data for request
            self.transaction.add_transaction_step('recharge phone', 'recharge_h20', 'S', 'Requesting recharge from h20')
            br.open('https://www.h2owirelessnow.com/mainControl.php?page=rechargeA')
            br.select_form("frm")
            br.form.set_all_readonly(False)
            br.form['mdn'] = str(self.transaction.autorefill.phone_number)
            br.form['pin'] = str(self.transaction.pin)
            response = br.submit()
            # Check for error response page
            nextpage = response.read().split("'")[1]
            if nextpage == 'mainControl.php?page=notifyError':
                result = br.open('https://www.h2owirelessnow.com/%s' % nextpage)
                result_info = BeautifulSoup(result.read())
                raise Exception(result_info.find("div", id="mainCont").contents[1].contents[3].string)
            self.transaction.add_transaction_step('recharge phone', 'end', 'S', "Phone recharged successfully")
        except Exception, msg:
            logger.error("Exception: %s. Trace: %s." % (msg, traceback.format_exc(limit=10)))
            self.transaction.add_transaction_step('recharge phone', 'end', 'E', u'%s' % msg)
            self.transaction.save()
            raise Exception(u'%s' % msg)

    def approved_link(self):
        if settings.TEST_MODE:
            self.transaction.add_transaction_step('recharge phone', 'requesting_approved_link', 'S', 'Requesting on Approved Link website in TEST mode')
            self.transaction.add_transaction_step('recharge phone', 'recharge_approved_link', 'S', 'Requesting recharge from Approved Link in TEST mode')
            return False
        try:
            self.transaction.add_transaction_step('recharge phone', 'requesting_approved_link', 'S', 'Requesting on Approved Link website')
            if requests.post('http://75.99.53.250/CVWebService/PhoneHandler.aspx?method=ChargePVC&phonenumber=%s&'
                             'pvcnumber=%s' % (self.transaction.autorefill.phone_number,
                                               self.transaction.pin)).text != 'CHARGE_STATUS=FAILED':
                self.transaction.add_transaction_step('recharge phone', 'recharge_approved_link', 'S', 'Charge successful')
            else:
                self.transaction.add_transaction_step('recharge phone', 'recharge_approved_link', 'S', 'Charge has been failed')
        except Exception, msg:
            logger.error("Exception: %s. Trace: %s." % (msg, traceback.format_exc(limit=10)))
            self.transaction.add_transaction_step('recharge phone', 'end', 'E', u'%s' % msg)
            self.transaction.save()
            raise Exception(u'%s' % msg)

    def custom_redirect_url(self, redirect, id=None):
        if not id:
            id = ''
        redirect_url = '%s/%s/%s' % (settings.SITE_DOMAIN, redirect, id)
        return redirect_url