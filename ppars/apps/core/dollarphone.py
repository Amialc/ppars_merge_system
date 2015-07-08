import logging
import traceback
from django.conf import settings
import requests
from requests.auth import HTTPBasicAuth
from suds.transport.https import WindowsHttpAuthenticated
from suds.xsd.doctor import ImportDoctor, Import
from suds.client import Client as WSClient
from BeautifulSoup import BeautifulSoup
import time
import json
import mechanize
import cookielib
from notifications import messages
from ppars.apps.core.models import CompanyProfile
from ppars.apps.notification.models import Notification


logger = logging.getLogger('ppars')
COMPLETED = "Completed"
FAILED = "Fulfillment Failed"

dp_response_codes = {
    -1: 'Invalid Login',
    -2: 'Invalid Login',
    -6: 'Invalid offering',
    -34: 'Account past due',
    -35: 'Transaction exceeds credit limit',
    -40: 'Invalid Phone number',
    -41: 'Invalid amount',
    -42: 'Invalid Product',
    -400: 'Invalid phone number',
    -401: 'Processing error',
    -402: 'Transaction already completed',
    -403: 'Invalid transaction amount',
    -404: 'Invalid product',
    -405: 'Duplicate transaction',
    -406: 'Invalid Transaction Id',
    -407: 'Denomination currently unavailable',
    -408: 'Transaction amount limit exceeded',
    -409: 'Destination Account is not prepaid',
    -410: 'Handset was reloaded within the last 10 minutes',
    -411: 'TopUp refused',
}


def login_site(url, url_success, user, user_tag, passw, passw_tag):
    br = mechanize.Browser()
    cj = cookielib.LWPCookieJar()
    br.set_handle_equiv(True)
    br.set_handle_redirect(True)
    br.set_handle_referer(True)
    br.set_handle_robots(False)
    br.set_handle_refresh(mechanize._http.HTTPRefreshProcessor(), max_time=1)
    br.addheaders = [('User-agent',
                      'Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.1) Gecko/2008071615 Fedora/3.0.1-1.fc9 Firefox/3.0.1')]
    br.open(url)
    br.select_form(nr=0)
    br.form[user_tag] = user
    br.form[passw_tag] = passw
    br.submit()
    if br.geturl() not in url_success:
        raise Exception("Failed to login to %s, please check the credentials" % url)
    return br


def dpapi_purchase_pin(form_fields):
    if settings.TEST_MODE:
        status = 1
        adv_status = "on Test mode"
        pin = 111222
        return status, adv_status, pin
    status = 0
    pin = None
    adv_status = ''
    try:
        ntlm = WindowsHttpAuthenticated(username='DPWEB-1\\%s' % form_fields['username'],
                                        password=form_fields['password'])
        imp = Import('http://schemas.xmlsoap.org/soap/encoding/')
        imp = Import('http://www.w3.org/2001/XMLSchema')
        imp.filter.add('https://dollarphone.com/PMAPI/PinManager')
        doctor = ImportDoctor(imp)
        try:
            client = WSClient("https://www.dollarphone.com/pmapi/PinManager.asmx?WSDL", transport=ntlm, doctor=doctor)
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            raise Exception('Failed to initialize dollarphone api, please verify your credentials')
        req = client.factory.create('TopUpReqType')
        action = client.factory.create('TopUpAction')
        req.Action = action.PurchasePin
        req.OfferingId = form_fields['OfferingId']
        req.Amount = form_fields['Amount']
        req.ProviderId = form_fields['ProviderId']
        try:
            request_pin = client.service.TopUpRequest(req)
        except Exception, e:
            raise Exception('Failed to initialize dollarphone api, please verify your credentials')
        if request_pin.responseCode < 0:
            raise Exception('%s' % dp_response_codes[request_pin.responseCode])
        if request_pin.TransId == 0:
            raise Exception('Dollarphone API retuned a 0 TransId, please contact Dollarphone to check your setup.')
        time.sleep(10)
        while True:
            pin_status = client.service.TopupConfirm(request_pin.TransId)
            if pin_status.Status == 'Success':
                status = 1
                pin = pin_status.PIN
                break
            elif pin_status.Status == 'Failed':
                raise Exception('Dollar phone transaction %s failed, with message %s' % (
                    request_pin.TransId, dp_response_codes[pin_status.ErrorCode]))
            time.sleep(2)
        adv_status = 'Pin %s extracted from Dollar Phone in transaction %s' % (pin, request_pin.TransId)
    except Exception, e:
        adv_status = "Failure :%s" % e
        logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
    finally:
        return status, adv_status, pin


def dpsite_purchase_pin(form_fields):
    if settings.TEST_MODE:
        status = 1
        adv_status = "on Test mode"
        pin = 111222
        return status, adv_status, pin
    status = 0
    pin = None
    adv_status = ''
    status_code = 0
    try:
        br = login_site(
            'https://www.dollarphonepinless.com/sign-in',
            ['https://www.dollarphonepinless.com/dashboard'],
            form_fields['username'],
            'user_session[email]',
            form_fields['password'],
            'user_session[password]'
        )
        # logger.debug("Dolarphone url: %s" % br.geturl())
        br.follow_link(br.links(text='Domestic PIN').next())
        br.select_form(nr=0)
        br.form.new_control('text', 'ppm_order[skip_check_for_duplicates]', {'value': ''})
        br.form.fixup()
        br.form['ppm_order[skip_check_for_duplicates]'] = '1'
        br.form['ppm_order[country_code]'] = ['US']
        carrier = br.find_control('ppm_order[prepaid_mobile_product_group_id]', type="select")
        for item in carrier.items:
            if item.get_labels()[0].text == form_fields['Carrier']:
                br.form['ppm_order[prepaid_mobile_product_group_id]'] = [item.name]
                break
        plan_name = br.find_control('ppm_order[prepaid_mobile_product_id]', type="select")
        for item in plan_name.items:
            if item.get_labels()[0].text == form_fields['Plan']:
                br.form['ppm_order[prepaid_mobile_product_id]'] = [item.name]
                break
        plan_cost = br.find_control('ppm_order[face_amount]', type="select")
        for item in plan_cost.items:
            if item.get_labels()[0].text == form_fields['Amount']:
                br.form['ppm_order[face_amount]'] = [item.name]
                break
        br.submit()
        try:
            br.select_form(nr=0)
            pin_url = br.geturl()
            br.submit()
            logger.debug("Dolarphone confirm: %s. Page: %s." % (br.geturl(), br.response().read()))
            if str(br.geturl()).endswith('/processing'):
                receipt_url = str(br.geturl()).replace('https://www.dollarphonepinless.com', '').replace('processing', '')
                logger.debug('receipt_url %s' % receipt_url)
                time.sleep(10)
            elif br.geturl() in pin_url:
                message = scrapping_dollar_phone_errors(br.response().read())
                if not message:
                    message = 'Undefined response from DollarPhone. Please check ' \
                              'order at DollarPhone site and you can add pin' \
                              ' to Unused'
                status_code = br.response().code
                raise Exception(message)
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            raise Exception(u'%s' % e)
        if not receipt_url:
            status_code = br.response().code
            raise Exception("Failed to request PIN, unexpected response from dollarphone website.")
        time.sleep(10)
        co = 0
        while True:
            co += 1
            br.open(u'https://www.dollarphonepinless.com%scheck_status.json' % receipt_url)
            pin_status = json.loads(br.response().read())
            if pin_status['status'] == COMPLETED:
                br.open('https://www.dollarphonepinless.com%sreceipt' % receipt_url)
                receipt = br.response().read()
                break
            elif pin_status['status'] == FAILED:
                status_code = br.response().code
                raise Exception(
                    'Get Pin request failed, check the <a target="blank"'
                    ' href="https://www.dollarphonepinless.com%sreceipt">receipt</a> for more information' % receipt_url)
            if co > 10:
                status_code = br.response().code
                raise Exception('Dollar phone transaction no response')
            time.sleep(5)
        soup = BeautifulSoup(receipt)
        table = soup.find("table")
        for row in table.findAll('tr')[1:]:
            col = row.findAll('td')
            if col[0].string.strip() == 'PIN:':
                status = 1
                pin = col[1].div.strong.string.strip()
                break
        adv_status = 'Pin %s extracted from Dollar Phone, details are at <a target="blank"' \
                     ' href="https://www.dollarphonepinless.com%sreceipt">receipt</a>.' % (pin, receipt_url)
    except Exception, e:
        answer = scrapping_cash_balance_and_send_email(form_fields['username'], form_fields['password'], form_fields['company'], status_code)
        adv_status = "Failure :%s %s" % (e, answer)
        logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
    finally:
        return status, adv_status, pin


def dpapi_topup(form_fields):
    response_status = 0
    adv_status = "dpapi_topup failed"
    try:
        ntlm = WindowsHttpAuthenticated(username='DPWEB-1\\%s' % form_fields['username'],
                                        password=form_fields['password'])
        imp = Import('http://schemas.xmlsoap.org/soap/encoding/')
        imp = Import('http://www.w3.org/2001/XMLSchema')
        imp.filter.add('https://dollarphone.com/PMAPI/PinManager')
        doctor = ImportDoctor(imp)
        try:
            client = WSClient("https://www.dollarphone.com/pmapi/PinManager.asmx?WSDL", transport=ntlm, doctor=doctor)
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            raise Exception('Failed to initialize dollarphone api, please verify your credentials')
        req = client.factory.create('TopUpReqType')
        action = client.factory.create('TopUpAction')
        req.Action = action.AddFunds
        req.PhoneNumber = form_fields['PhoneNumber']
        req.OfferingId = form_fields['OfferingId']
        req.Amount = form_fields['Amount']
        req.ProviderId = form_fields['ProviderId']
        try:
            request_pin = client.service.TopUpRequest(req)
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            raise Exception('Failed to initialize dollarphone api, please verify your credentials')
        if request_pin.responseCode < 0:
            raise Exception('%s' % dp_response_codes[request_pin.responseCode])
        if request_pin.TransId == 0:
            raise Exception('Dollarphone API retuned a 0 TransId, please contact Dollarphone to check your setup.')
        time.sleep(10)
        co = 0
        while True:
            co += 1
            status = client.service.TopupConfirm(request_pin.TransId)
            logger.debug('status %s' % status)
            if status.Status == 'Success':
                response_status = 1
                break
            elif status.Status == 'Failed':
                raise Exception('Dollar phone transaction %s failed, with message %s' % (
                    request_pin.TransId, dp_response_codes[status.ErrorCode]))
            if co > 10:
                raise Exception('Dollar phone transaction %s no response' % (request_pin.TransId, ))
            time.sleep(4)
        adv_status = 'Phone topped up successfully, with dollarphone transaction %s' % request_pin.TransId
    except Exception, e:
        adv_status = "Failure :%s" % e
        logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
    finally:
        return response_status, adv_status


def dpsite_topup(form_fields):
    response_status = 0
    status_code = 0
    adv_status = "dpsite_topup failed"
    receipt_url = ''
    try:
        br = login_site(
            'https://www.dollarphonepinless.com/sign-in',
            ['https://www.dollarphonepinless.com/dashboard'],
            form_fields['username'],
            'user_session[email]',
            form_fields['password'],
            'user_session[password]'
        )
        br.follow_link(br.links(text='Domestic Top-Up').next())
        br.select_form(nr=0)
        br.form['ppm_order[country_code]'] = ['US']
        br.form['ppm_order[prepaid_mobile_phone]'] = form_fields['PhoneNumber']
        carrier = br.find_control('ppm_order[prepaid_mobile_product_group_id]', type="select")
        for item in carrier.items:
            if item.get_labels()[0].text == form_fields['Carrier']:
                br.form['ppm_order[prepaid_mobile_product_group_id]'] = [item.name]
                break
        plan_name = br.find_control('ppm_order[prepaid_mobile_product_id]', type="select")
        for item in plan_name.items:
            if item.get_labels()[0].text == form_fields['Plan']:
                br.form['ppm_order[prepaid_mobile_product_id]'] = [item.name]
                break
        plan_cost = br.find_control('ppm_order[face_amount]', type="select")
        for item in plan_cost.items:
            if item.get_labels()[0].text == form_fields['Amount']:
                br.form['ppm_order[face_amount]'] = [item.name]
                break
        br.submit()
        try:
            br.select_form(nr=0)
            pin_url = br.geturl()
            br.submit()
            logger.debug('geturl %s' % br.geturl())
            # receipt_url = br.response().read().split(':')[1].split("'")[1].split('processing')[0]
            if str(br.geturl()).endswith('/processing'):
                receipt_url = str(br.geturl()).replace('https://www.dollarphonepinless.com', '').replace('processing', '')
                logger.debug('receipt_url %s' % receipt_url)
            elif br.geturl() in pin_url:
                message = scrapping_dollar_phone_errors(br.response().read())
                if not message:
                    message = 'Undefined response from DollarPhone. Please check ' \
                              'order at DollarPhone site and you can add pin' \
                              ' to Unused'
                status_code = br.response().code
                raise Exception(message)
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            raise Exception(u'%s' % e)
        if not receipt_url:
            status_code = br.response().code
            raise Exception("Failed to topup phone, unexpected response from dollarphone website.")
        time.sleep(10)
        co = 0
        while True:
            co += 1
            br.open(u'https://www.dollarphonepinless.com%scheck_status.json' % receipt_url)
            pin_status = json.loads(br.response().read())
            logger.debug('pin_status %s' % pin_status)
            if pin_status['status'] == COMPLETED:
                response_status = 1
                break
            elif pin_status['status'] == FAILED:
                status_code = br.response().code
                raise Exception(
                    'Topup phone failed, details are at <a target="blank" href="https://www.dollarphonepinless.com%sreceipt">receipt</a>.' % receipt_url)
            if co > 10:
                status_code = br.response().code
                raise Exception('Dollar phone transaction no response')
            time.sleep(4)
        adv_status = 'Topup successful, details are at <a target="blank" href="https://www.dollarphonepinless.com%sreceipt">receipt</a>.' % receipt_url
    except Exception, e:
        answer = scrapping_cash_balance_and_send_email(form_fields['username'], form_fields['password'], form_fields['company'], status_code)
        adv_status = "Failure :%s %s" % (e, answer)
        logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
    finally:
        return response_status, adv_status


def dpsite_get_pin_charge(form_fields):
    # username
    # password
    # Carrier
    # Plan
    # Cost
    # Customer
    if settings.TEST_MODE:
        adv_status = "Dollar Phone on Test mode"
        pin = 111222
        receipt_id = 123456
        return receipt_id, adv_status, pin
    pin = None
    adv_status = ''
    receipt_id = None
    authenticity_token = ''
    carrier_id = ''
    plan_id = ''
    cost_id = ''
    reference_number = ''
    status_code = 0
    try:
        s = requests.Session()
        s.get('https://www.dollarphonepinless.com/sign-in',
              auth=HTTPBasicAuth(form_fields['username'], form_fields['password']))
        url_success = 'https://www.dollarphonepinless.com/dashboard'
        r = s.get(url_success)
        if r.url not in url_success:
            status_code = r.status_code
            raise Exception("Failed to login to Dollar Phone, please check the credentials")
        r = s.get('https://www.dollarphonepinless.com/prepaid_mobile_orders/domestic/pin/new')
        soup = BeautifulSoup(r.text)
        authenticity_tokens = soup.findAll('input')
        for token in authenticity_tokens:
            if token.get('name') == 'authenticity_token':
                authenticity_token = token.get('value')
        carriers = soup.find('select', id="group-list")
        for carrier in carriers:
            try:
                if carrier.text.replace('&amp;', '&') == form_fields['Carrier']:
                    carrier_id = carrier.get('value')
                    break
            except AttributeError:
                pass
        plans = soup.find('select', id="product-list")
        for plan in plans:
            try:
                if plan.text.replace('&amp;', '&') == form_fields['Plan']:
                    plan_id = plan.get('value')
                    break
            except AttributeError:
                pass
        costs = soup.find('select', id="denomination-list")
        for cost in costs:
            try:
                if cost.text == form_fields['Amount']:
                    cost_id = cost.get('value')
                    break
            except AttributeError:
                pass
        data = {
            'authenticity_token': authenticity_token,
            'product_type': 'pin',
            'location': 'domestic',
            'ppm_order[prepaid_mobile_phone]': '',
            'ppm_order[use_secondary_funding_source]': '',
            'ppm_order[always_use_secondary_funding_source]': '',
            'ppm_order[skip_check_for_duplicates]': '',
            'ppm_order[country_code]': 'US',
            'ppm_order[prepaid_mobile_product_group_id]': carrier_id,
            'ppm_order[prepaid_mobile_product_id]': plan_id,
            'ppm_order[face_amount]': cost_id,
            'ppm_order[payment_option]': 'Customer Credit Card',
            'commit': "Continue",
        }
        pin_order_url = 'https://www.dollarphonepinless.com/ppm_orders/confirm?location=domestic&product_type=pin'
        r = s.post(pin_order_url, data)
        soup = BeautifulSoup(r.text)
        reference_numbers = soup.findAll('input')
        for token in reference_numbers:
            if token.get('name') == 'ppm_order[reference_number]':
                reference_number = token.get('value')
        order = {
            'commit': 'Process Order',
            'ppm_order[reference_number]': reference_number,
            'ppm_order[single_use_credit_card_attributes][address1]': form_fields['Customer'].address,
            'ppm_order[single_use_credit_card_attributes][card_number]': form_fields['Customer'].get_local_card().number,
            'ppm_order[single_use_credit_card_attributes][city]': form_fields['Customer'].city,
            'ppm_order[single_use_credit_card_attributes][country]': 'US',
            'ppm_order[single_use_credit_card_attributes][email]': form_fields['Customer'].primary_email,
            'ppm_order[single_use_credit_card_attributes][expires_on(1i)]': form_fields['Customer'].get_local_card().expiration_year,
            'ppm_order[single_use_credit_card_attributes][expires_on(2i)]': form_fields['Customer'].get_local_card().expiration_month,
            'ppm_order[single_use_credit_card_attributes][expires_on(3i)]': '1',
            'ppm_order[single_use_credit_card_attributes][name]': form_fields['Customer'].full_name,
            'ppm_order[single_use_credit_card_attributes][phone]': '',
            'ppm_order[single_use_credit_card_attributes][state]': 'NY',
            'ppm_order[single_use_credit_card_attributes][verification_value]': form_fields['Customer'].get_local_card().cvv,
            'ppm_order[single_use_credit_card_attributes][zip]': form_fields['Customer'].zip,
            'ppm_order[sms_receipt_locale]': 'en',
            'ppm_order[sms_receipt_phone_number]': '',
        }
        data.update(order)
        pin_url = 'https://www.dollarphonepinless.com/ppm_orders'
        r = s.post(pin_url, data)
        logger.debug('url %s Page %s' % (r.url, r.text))
        if str(r.url).endswith('/processing'):
            receipt_id = str(r.url).replace('https://www.dollarphonepinless.com/ppm_orders/', '').replace('/processing', '')
            time.sleep(10)
        elif r.url in pin_url:
            message = scrapping_dollar_phone_errors(r.text, s=s)
            if not message:
                message = 'Undefined response from DollarPhone. Please check ' \
                          'order at DollarPhone site and you can add pin' \
                          ' to Unused'
            status_code = r.status_code
            logger.debug('status_code %s' % status_code)
            raise Exception(message)
        co = 0
        while True:
            co += 1
            r = s.get(u'https://www.dollarphonepinless.com/ppm_orders/%s/check_status.json' % receipt_id)
            logger.debug('url %s Page %s' % (r.url, r.text))
            pin_status = json.loads(r.text)
            if pin_status['status'] == COMPLETED:
                r = s.get('https://www.dollarphonepinless.com/ppm_orders/%s/receipt' % receipt_id)
                receipt = r.text
                break
            elif pin_status['status'] == FAILED:
                status_code = r.status_code
                raise Exception(
                    'Get Pin request failed, check the '
                    '<a target="blank" '
                    'href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>'
                    ' for more information' % receipt_id)
            if co > 10:
                status_code = r.status_code
                raise Exception(
                    'Dollar phone transaction no response, check the '
                    '<a target="blank" '
                    'href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>'
                    ' for more information' % receipt_id)
            time.sleep(10)
        logger.debug('receipt %s' % receipt)
        soup = BeautifulSoup(receipt)
        table = soup.find("table")
        logger.debug('table %s' % table)
        for row in table.findAll('tr')[1:]:
            col = row.findAll('td')
            if col[0].string.strip() == 'PIN:':
                pin = col[1].div.strong.string.strip()
                break
        adv_status = 'Pin %s extracted from Dollar Phone, details are at ' \
                     '<a target="blank" href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>.' % \
                     (pin, receipt_id)
    except Exception, e:
        logger.debug('start')
        logger.debug('username %s password %s company %s code %s' % (form_fields['username'], form_fields['password'], form_fields['company'], status_code))
        answer = scrapping_cash_balance_and_send_email(form_fields['username'], form_fields['password'], form_fields['company'], status_code)
        logger.debug('answer: %s' % answer)
        logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
        adv_status = "Failure :%s %s" % (e, answer)
    finally:
        return receipt_id, adv_status, pin


def dpsite_top_up_charge(form_fields):
    # username
    # password
    # Carrier
    # Plan
    # Cost
    # Customer
    if settings.TEST_MODE:
        adv_status = "Dollar Phone on Test mode"
        receipt_id = 123456
        return receipt_id, adv_status
    adv_status = ''
    receipt_id = None
    authenticity_token = ''
    carrier_id = ''
    plan_id = ''
    cost_id = ''
    reference_number = ''
    status_code = 0
    try:
        s = requests.Session()
        s.get('https://www.dollarphonepinless.com/sign-in',
              auth=HTTPBasicAuth(form_fields['username'], form_fields['password']))
        url_success = 'https://www.dollarphonepinless.com/dashboard'
        r = s.get(url_success)
        if r.url not in url_success:
            status_code = r.status_code
            raise Exception("Failed to login to Dollar Phone, please check the credentials")
        r = s.get('https://www.dollarphonepinless.com/prepaid_mobile_orders/domestic/top_up/new')
        soup = BeautifulSoup(r.text)
        authenticity_tokens = soup.findAll('input')
        for token in authenticity_tokens:
            if token.get('name') == 'authenticity_token':
                authenticity_token = token.get('value')
        carriers = soup.find('select', id="group-list")
        for carrier in carriers:
            try:
                if carrier.text.replace('&amp;', '&') == form_fields['Carrier']:
                    carrier_id = carrier.get('value')
                    break
            except AttributeError:
                pass
        plans = soup.find('select', id="product-list")
        for plan in plans:
            try:
                if plan.text.replace('&amp;', '&') == form_fields['Plan']:
                    plan_id = plan.get('value')
                    break
            except AttributeError:
                pass
        costs = soup.find('select', id="denomination-list")
        for cost in costs:
            try:
                if cost.text == form_fields['Amount']:
                    cost_id = cost.get('value')
                    break
            except AttributeError:
                pass
        data = {
            'authenticity_token': authenticity_token,
            'product_type': 'top_up',
            'location': 'domestic',
            'ppm_order[notification_locale]': 'en',
            'ppm_order[notification_phone_number]': '',
            'ppm_order[prepaid_mobile_phone]': form_fields['phone_number'],
            'ppm_order[use_secondary_funding_source]': '',
            'ppm_order[always_use_secondary_funding_source]': '',
            'ppm_order[country_code]': 'US',
            'ppm_order[prepaid_mobile_product_group_id]': carrier_id,
            'ppm_order[prepaid_mobile_product_id]': plan_id,
            'ppm_order[face_amount]': cost_id,
            'ppm_order[payment_option]': 'Customer Credit Card',
            'commit': "Continue",
        }
        top_up_order_url = 'https://www.dollarphonepinless.com/ppm_orders/confirm?location=domestic&product_type=top_up'
        r = s.post(top_up_order_url, data)
        soup = BeautifulSoup(r.text)
        reference_numbers = soup.findAll('input')
        for token in reference_numbers:
            if token.get('name') == 'ppm_order[reference_number]':
                reference_number = token.get('value')
        order = {
            'commit': 'Process Order',
            'ppm_order[reference_number]': reference_number,
            'ppm_order[single_use_credit_card_attributes][address1]': form_fields['Customer'].address,
            'ppm_order[single_use_credit_card_attributes][card_number]': form_fields['Customer'].get_local_card().number,
            'ppm_order[single_use_credit_card_attributes][city]': form_fields['Customer'].city,
            'ppm_order[single_use_credit_card_attributes][country]': 'US',
            'ppm_order[single_use_credit_card_attributes][email]': form_fields['Customer'].primary_email,
            'ppm_order[single_use_credit_card_attributes][expires_on(1i)]': form_fields['Customer'].get_local_card().expiration_year,
            'ppm_order[single_use_credit_card_attributes][expires_on(2i)]': form_fields['Customer'].get_local_card().expiration_month,
            'ppm_order[single_use_credit_card_attributes][expires_on(3i)]': '1',
            'ppm_order[single_use_credit_card_attributes][name]': form_fields['Customer'].full_name,
            'ppm_order[single_use_credit_card_attributes][phone]': '',
            'ppm_order[single_use_credit_card_attributes][state]': 'NY',
            'ppm_order[single_use_credit_card_attributes][verification_value]': form_fields['Customer'].get_local_card().cvv,
            'ppm_order[single_use_credit_card_attributes][zip]': form_fields['Customer'].zip,
            'ppm_order[sms_receipt_locale]': 'en',
            'ppm_order[sms_receipt_phone_number]': '',
        }
        data.update(order)
        pin_url = 'https://www.dollarphonepinless.com/ppm_orders'
        r = s.post(pin_url, data)
        logger.debug('url %s Page %s' % (r.url, r.text))
        if str(r.url).endswith('/processing'):
            receipt_id = str(r.url).replace('https://www.dollarphonepinless.com/ppm_orders/', '').replace('/processing','')
            time.sleep(10)
        elif r.url in pin_url:
            message = scrapping_dollar_phone_errors(r.text, s=s)
            if not message:
                message = 'Undefined response from DollarPhone. Please check ' \
                          'order at DollarPhone site and you can add pin' \
                          ' to Unused'
            status_code = r.status_code
            raise Exception(message)
        co = 0
        while True:
            co += 1
            r = s.get(u'https://www.dollarphonepinless.com/ppm_orders/%s/check_status.json' % receipt_id)
            logger.debug('url %s Page %s' % (r.url, r.text))
            pin_status = json.loads(r.text)
            if pin_status['status'] == COMPLETED:
                r = s.get('https://www.dollarphonepinless.com/ppm_orders/%s/receipt' % receipt_id)
                receipt = r.text
                break
            elif pin_status['status'] == FAILED:
                status_code = r.status_code
                raise Exception(
                    'Get Pin request failed, check the '
                    '<a target="blank" '
                    'href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>'
                    ' for more information' % receipt_id)
            if co > 10:
                status_code = r.status_code
                raise Exception(
                    'Dollar phone transaction no response, check the '
                    '<a target="blank" '
                    'href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>'
                    ' for more information' % receipt_id)
            time.sleep(10)
        logger.debug('receipt %s' % receipt)
        # soup = BeautifulSoup(receipt)
        # table = soup.find("table")
        # logger.debug('table %s' % table)
        # for row in table.findAll('tr')[1:]:
        # col = row.findAll('td')
        #     if col[0].string.strip() == 'PIN:':
        #         pin = col[1].div.strong.string.strip()
        #         break
        adv_status = 'Phone was refilled, details are at ' \
                     '<a target="blank" href="https://www.dollarphonepinless.com/ppm_orders/%s/receipt">receipt</a>.' % receipt_id
    except Exception, e:
        answer = scrapping_cash_balance_and_send_email(form_fields['username'], form_fields['password'], form_fields['company'], status_code)
        logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
        adv_status = "Failure :%s %s" % (e, answer)
        raise Exception(adv_status)
    return receipt_id, adv_status


def scrapping_dollar_phone_errors(page, **kwargs):
    message = ''
    soup = BeautifulSoup(page)
    if '/authentication_required.html' in page:
        message = messages['dollar_phone_errors']['enter_digit_token']
        # making logs for authentifications
        try:
            for script in soup.findAll('script'):
                page = str(script)
                if 'form#new_ppm_order' in page:
                    authentification_url = page[page.index('href: "'): page.index('", type:')].replace('href: "', 'https://www.dollarphonepinless.com')
                    logger.debug('authentification_url %s' % authentification_url)
                    result_page = kwargs['s'].get(authentification_url).text
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
            result_page = '%s ' % e
        logger.debug('Authentification page %s' % result_page)
    elif 'error-explanation' in page:
        h = soup.find('div', id='error-explanation')
        for h1 in h.findAll('div'):
            if h1.get('class') == 'error-messages red':
                message = '%s' % h1.text
        d = soup.find('div', id='credit-card-information')
        for p1 in d.findAll('p'):
            if p1.get('class') == 'inline-errors':
                message = '%s %s %s' % (message, p1.parent.label.text, p1.text)
    elif 'dialog' in page:
        for div in soup.findAll('div'):
            if div.get('class') == 'dialog':
                h1 = div.find('h1')
                p = div.find('p')
                message = '%s %s' % (h1.text, p.text)
    logger.debug('After scrapping eerro message: %s' % message)
    return message


def scrapping_cash_balance_and_send_email(username, password, company, status_code):
    body = ''
    try:
        if status_code == 404:
            logger.debug('status_code ' % status_code)
            cash_balance = ''
            s = requests.Session()
            s.get('https://www.dollarphonepinless.com/sign-in',
                  auth=HTTPBasicAuth(username, password))
            url_success = 'https://www.dollarphonepinless.com/dashboard'
            r = s.get(url_success)
            if r.url not in url_success:
                raise Exception("Failed to login to Dollar Phone, please check the credentials")
            soup = BeautifulSoup(r.text)
            account_summary = soup.find(id='balance')
            for td in account_summary.findAll('td'):
                if td.get('class') == 'data-cell':
                    cash_balance_text = td.text[1:]
                    cash_balance = float(cash_balance)
                if cash_balance == 0:
                    subject = "insufficient funds in the DollarPhone account"
                    body = "You not enough money for new pin!" \
                           " Please refill your cash on Dollarphone! Your CashBalance is $%s!" % cash_balance
                    notification = Notification.objects.create(
                        company=Notification.objects.get(company=CompanyProfile.objects.get(superuser_profile=True)),
                        email=company.email_id,
                        subject=subject,
                        body=body,
                        send_with=Notification.MAIL
                    )
                    notification.send_notification()
    except Exception, e:
        logger.debug('error: "%s" meddage "%s"' % (e, body))
        logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
    finally:
        return body