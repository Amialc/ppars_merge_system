import logging
import traceback
from django.conf import settings
from django.core.urlresolvers import reverse
from ppars.apps.charge.models import Charge, ChargeStep, TransactionCharge
from ppars.apps.core.models import Transaction, UserProfile, CompanyProfile, \
    TransactionStep, TransactionError, Customer
from ppars.apps.notification.models import Notification, CustomPreChargeMessage
from notifications import messages

logger = logging.getLogger('ppars')


class SendNotifications:

    def __init__(self, id):
        self.transaction = Transaction.objects.get(id=id)
        self.company = UserProfile.objects.get(user=self.transaction.user).company
        self.transaction.current_step = 'send_notifications'

    def main(self):
        self.send_sc_report()
        self.send_to_asana()
        return self.transaction

    def send_sc_report(self):
        if self.company.use_sellercloud:
            try:
                self.transaction.send_tratsaction_to_sellercloud()
                self.transaction.send_note_to_sellercloud_order()
                self.transaction.send_payment_to_sellercloud_order()
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
                self.transaction.add_transaction_step('notification', 'SellerCloud', 'E', u'%s' % e)
                raise Exception(u'%s' % e)

    def send_to_asana(self):
        if self.company.use_asana:
            try:
                self.transaction.send_asana()
            except Exception, e:
                logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))
                self.transaction.add_transaction_step('notification', 'Asana', 'E', u'%s' % e)
                raise Exception(u'%s' % e)

    def send_statusmail(self):
        send_company_refill_notification(self.transaction)
        send_customer_refill_notification(self.transaction)


def check_message_customer(msg):
    if msg == messages['dollar_phone_errors']['enter_digit_token']:
        return False

    '''
    Creating list of exist words in message, then compare correctly

    1. Iterate words in message
    2. If msg has word, then adding to list TRUE else FALSE
    3. If this list hasn't False, then return False
    '''
    if False not in [word.lower() in msg.lower() for word in messages['queue_precharge']['customer_failed']['not_charge']]:
        return True

    if False not in [word.lower() in msg.lower() for word in messages['queue_precharge']['customer_failed']['invalid_cvc']]:
        return True

    if False not in [word.lower() in msg.lower() for word in messages['queue_precharge']['customer_failed']['billing_adr']]:
        return True

    return False


def check_message_company(msg):
    if True in [word.lower() in msg.lower() for word in messages['queue_precharge']['company_failed']['pin']] and \
       True in [word.lower() in msg.lower() for word in messages['queue_precharge']['company_failed']['failed']]:
        return True

    return False


def successful_subject(transaction):
    return "[%s] Status of successful transaction %s" % \
           (transaction.company.company_name,
            transaction.id)


def failed_subject(transaction):
    if transaction.company.precharge_failed_email and 'check_payment' == transaction.current_step:
        subject = "[%s] Credit Card charge failed for %s" % \
                  (transaction.company.company_name, transaction.id)
    else:
        subject = "[%s] Status of failed transaction %s" % \
                  (transaction.company.company_name, transaction.id)
    return subject


def successful_company_body(transaction):
    super_company = CompanyProfile.objects.get(superuser_profile=True)
    if transaction.autorefill.refill_type == "GP":
        body = "Hi %s,<br/><br/>Get Pin for customer %s and plan %s successful. " \
               "Pin <a href=\"%s\">%s</a> was extracted. <br/><br/>Regards, %s" % \
               (transaction.user,
                transaction.customer,
                transaction.autorefill.plan,
                transaction.get_full_url(),
                transaction.pin,
                super_company.company_name)
    else:
        body = "Hi %s,<br/><br/>Customer %s's phone %s has been refilled/topped " \
               "successfully with pin %s for plan %s.<br/>Details in " \
               "<a href=\"%s\"Transaction</a>.<br/><br/>Regards, %s" % \
               (transaction.user,
                transaction.customer,
                transaction.autorefill.phone_number,
                transaction.pin,
                transaction.autorefill.plan,
                transaction.get_full_url(),
                super_company.company_name)
    return body


def failed_company_body(transaction):
    super_company = CompanyProfile.objects.get(superuser_profile=True)
    pin = ''
    if transaction.pin:
        pin = ' with pin %s' % transaction.pin
    if transaction.autorefill.refill_type == "GP":
        body = "Hi %s,<br/><br/>Get Pin%s for customer %s and plan %s failed. " \
               "<a href=\"%s\">Transaction</a> failed due to \"%s\" .<br/></br>" \
               "Regards, %s" % \
               (transaction.user,
                pin,
                transaction.customer,
                transaction.autorefill.plan,
                transaction.get_full_url(),
                transaction.adv_status,
                super_company.company_name)
    else:
        body = 'Hi %s,<br/><br/>System failed to refill customer %s`s phone %s ' \
               'with plan %s%s. <a href=\"%s\">Transaction</a> errored ' \
               'in step %s due to \"%s\".<br/> This transaction has been declined.' \
               '<br/><br/>Regards, %s' % \
               (transaction.user,
                transaction.customer,
                transaction.autorefill.phone_number,
                transaction.autorefill.plan,
                pin,
                transaction.get_full_url(),
                transaction.current_step,
                transaction.adv_status,
                super_company.company_name)
    return body


def successful_customer_body(transaction):
    if transaction.autorefill.refill_type == "GP":
        body = "Hi %s,<br/><br/>Pin %s has been generated for plan \"%s[%s]\"." \
               "<br/><br/>Thanks for your business.<br/>%s" % \
               (transaction.customer,
                transaction.pin,
                transaction.autorefill.plan.plan_name,
                transaction.cost,
                transaction.company.company_name)
    else:
        body = "Hi %s,<br/><br/>We have successfully refilled/topped up your" \
               " phone %s with pin %s for plan \"%s[%s]\".<br/><br/>" \
               "Thanks for your business.<br/>%s" % \
               (transaction.customer,
                transaction.autorefill.phone_number,
                transaction.pin,
                transaction.autorefill.plan.plan_name,
                transaction.cost,
                transaction.company.company_name)
    return body


def failed_customer_body(transaction):
    pin = ''
    if transaction.pin:
        pin = ' with pin %s' % transaction.pin
    if transaction.autorefill.refill_type == "GP":
        body = 'Hi %s,<br/><br/> Our system is facing issues generating a ' \
               'pin %s for the plan \"%s(%s)\".<br/>A live person will be ' \
               'notified and will resolve it ASAP. Thanks for being patient.' \
               '<br/><br/> Thanks for your business,<br/>%s' % \
               (transaction.customer,
                pin,
                transaction.autorefill.plan.plan_name,
                transaction.cost,
                transaction.company.company_name)
    else:
        body = 'Hi %s,<br/><br/> Our system is facing issues ' \
               'refilling/topping up your phone %s with plan \"%s(%s)\"%s.' \
               '<br/>A live person will be notified and will resolve it ASAP. ' \
               'Thanks for being patient.' \
               '<br/></br>Thanks for your business,</br>%s' % \
               (transaction.customer,
                transaction.autorefill.phone_number,
                transaction.autorefill.plan.plan_name,
                transaction.cost,
                pin,
                transaction.company.company_name)
    return body


def send_company_refill_notification(transaction):
    super_company = CompanyProfile.objects.get(superuser_profile=True)
    if Transaction.SUCCESS == transaction.status:
        if not transaction.company.email_success:
            transaction.add_transaction_step('notification',
                                             'send user mail',
                                             TransactionStep.SUCCESS,
                                             'User refused to receive reports '
                                             'of successful transactions')
            return False
        subject = successful_subject(transaction)
        body = successful_company_body(transaction)
    else:
        subject = failed_subject(transaction)
        body = failed_company_body(transaction)
    try:
        send_notification(transaction,
                          super_company,
                          subject,
                          body,
                          transaction.company.email_id,
                          Notification.MAIL)
        transaction.add_transaction_step('notification',
                                         'send user mail',
                                         TransactionStep.SUCCESS,
                                         'Message was sent to the user')
    except Exception, e:
        transaction.add_transaction_step('notification',
                                         'send user mail',
                                         TransactionStep.ERROR,
                                         'Message wasn`t sent to the user, '
                                         'because: "%s"' % e)


def send_customer_refill_notification(transaction):
    # checking customer notification status and available tokens
    if transaction.customer.send_status == Customer.DONT_SEND:
        transaction.add_transaction_step('notification',
                                         'not send notifications to customer',
                                         TransactionStep.SUCCESS,
                                         'Customer refused to get any '
                                         'information messages')
        return False
    if (Notification.MAIL == transaction.customer.send_status and
            (not transaction.company.mandrill_key or
             not transaction.company.mandrill_email or
             not transaction.customer.primary_email)):
        transaction.add_transaction_step('notification',
                                         'send email to customer',
                                         TransactionStep.ERROR,
                                         'Mandrill email tokens or customer '
                                         'email is not specified')
        return False
    elif (Notification.SMS == transaction.customer.send_status and
            (not transaction.company.twilio_sid or
             not transaction.company.twilio_auth_token or
             not transaction.company.twilio_number)):
        transaction.add_transaction_step('notification',
                                         'send sms to customer ',
                                         TransactionStep.ERROR,
                                         'Twilio account is missing in company')
        return False
    elif (Notification.SMS_EMAIL == transaction.customer.send_status and
            (not transaction.company.mandrill_key or
             not transaction.company.mandrill_email or
             not transaction.customer.sms_email)):
        transaction.add_transaction_step('notification',
                                         'send sms via email to customer',
                                         TransactionStep.ERROR,
                                         'Mandrill email tokens not exist or '
                                         'customer sms email phone number is'
                                         ' empty')
        return False
    if Transaction.SUCCESS == transaction.status:
        if not transaction.customer.email_success:
            transaction.add_transaction_step('notification',
                                             'not send notifications to customer',
                                             TransactionStep.SUCCESS,
                                             'Customer refused to get '
                                             'successful information messages')
            return False
        subject = successful_subject(transaction)
        body = successful_customer_body(transaction)
    else:
        transaction_error, created = TransactionError.objects.get_or_create(transaction=transaction,
                                                                            message=transaction.adv_status,
                                                                            step=transaction.current_step)
        if created:
            subject = failed_subject(transaction)
            body = failed_customer_body(transaction)
        else:
            return False
    try:
        send_notification(transaction,
                          transaction.company,
                          subject,
                          body,
                          transaction.customer.primary_email,
                          transaction.customer.send_status)
        transaction.add_transaction_step('notification',
                                         'send %s to customer' % transaction.customer.get_send_status_display(),
                                         TransactionStep.SUCCESS,
                                         'Message was sent to the customer')
    except Exception, e:
        transaction.add_transaction_step('notification',
                                         'send to customer',
                                         TransactionStep.ERROR,
                                         'Message wasn`t sent to the customer, '
                                         'because: "%s"' % e)


def send_notification(transaction, company, subject, body, email, send_with):
    notification = Notification.objects.create(
        company=company,
        customer=transaction.customer,
        email=email,
        phone_number=transaction.autorefill.phone_number,
        subject=subject,
        body=body,
        send_with=send_with)
    notification.send_notification()


def successful_precharge_customer_notification(charge):
    subject = "[%s] Credit Card charge ended successfully" % (charge.company.company_name)
    body = 'Hi %s,<br/><br/>' \
           'We charge your card ending %s for your monthly refill of $%s.' \
           '<br/><br/>Regards, %s' % \
           (
               charge.customer.first_name,
               charge.creditcard[-4:],
               charge.amount,
               charge.company.company_name,
           )
    notification = Notification.objects.create(
        company=charge.company,
        customer=charge.customer,
        email=charge.customer.primary_email,
        phone_number=charge.autorefill.phone_number,
        subject=subject,
        body=body,
        send_with=charge.company.precharge_notification_type)
    try:
        notification.send_notification()
        charge.add_charge_step('precharge', Charge.SUCCESS,
                               'Send notification about successfully precharge'
                               ' to customer')
    except Exception, e:
        charge.add_charge_step('precharge', Charge.ERROR,
                               'Send notification about successfully precharge '
                               'to customer failed with error: "%s"' % e)


def successful_precharge_restart_customer_notification(charge):
    if ChargeStep.objects.filter(charge=charge, status=ChargeStep.SUCCESS,
                                 adv_status__icontains='Send notification with error to customer'):
        subject = "[%s] Credit Card charge retry ended successfully" % charge.company.company_name
        body = 'Hi %s,<br/><br/>' \
               'Hi, a retry of charge for $%s has succeeded so your phone (%s) will be refilled on $%s.' \
               '<br/><br/>Regards, %s' % \
               (
                   charge.customer.first_name,
                   charge.amount,
                   charge.autorefill.phone_number,
                   charge.autorefill.renewal_date,
                   charge.company.company_name,
               )
        notification = Notification.objects.create(
            company=charge.company,
            customer=charge.customer,
            email=charge.customer.primary_email,
            phone_number=charge.autorefill.phone_number,
            subject=subject,
            body=body,
            send_with=charge.company.precharge_notification_type)
        try:
            notification.send_notification()
            charge.add_charge_step('precharge', Charge.SUCCESS,
                                   'Send notification about successfully restarted precharge'
                                   ' to customer')
            if TransactionCharge.objects.filter(charge=charge):
                if TransactionCharge.objects.filter(charge=charge)[0].transaction:
                    TransactionCharge.objects.filter(charge=charge)[0].transaction.\
                        add_transaction_step(TransactionCharge.objects.filter(charge=charge)[0].transaction.current_step,
                                             'precharge', Transaction.SUCCESS,
                                             'Send notification about successfully restarted precharge'
                                             ' to customer')
        except Exception, e:
            charge.add_charge_step('precharge', Charge.ERROR,
                                   'Send notification about successfully restarted precharge '
                                   'to customer failed with error: "%s"' % e)
            if TransactionCharge.objects.filter(charge=charge):
                if TransactionCharge.objects.filter(charge=charge)[0].transaction:
                    TransactionCharge.objects.filter(charge=charge)[0].transaction.\
                        add_transaction_step(TransactionCharge.objects.filter(charge=charge)[0].transaction.current_step,
                                             'precharge', Transaction.ERROR,
                                             'Send notification about successfully restarted precharge '
                                             'to customer failed with error: "%s"' % e)


def failed_precharge_customer_notification(charge):
    subject = "[%s] Credit Card charge failed" % charge.company.company_name
    body = 'Hi %s,<br/><br/>' \
           'We attempted to charge your card ending %s for your monthly ' \
           'refill phone %s of $%s, but the transaction has failed. Please contact us ' \
           'within 2 days to avoid interruption of your cellphone service, ' \
           'our email is %s.<br/><br/>Regards, %s' % \
           (
               charge.customer.first_name,
               charge.creditcard[-4:],
               charge.autorefill.phone_number,
               charge.amount,
               charge.company.email_id,
               charge.company.company_name,
           )
    custom_precharge_message = CustomPreChargeMessage.objects.filter(use_message=True, company=charge.company)
    if custom_precharge_message:
        body = '%s<br/><br/>%s' % (body, custom_precharge_message[0].message)
    notification = Notification.objects.create(
        company=charge.company,
        customer=charge.customer,
        email=charge.customer.primary_email,
        phone_number=charge.autorefill.phone_number,
        subject=subject,
        body=body,
        send_with=charge.company.precharge_notification_type)
    try:
        notification.send_notification()
        charge.add_charge_step('precharge', Charge.SUCCESS,
                               'Send notification with error to customer')
    except Exception, e:
        charge.add_charge_step('precharge', Charge.ERROR,
                               'Send notification with error to customer '
                               'failed with error: "%s"' % e)


def failed_precharge_company_notification(charge):
    subject = "[%s] Credit Card precharge failed" % charge.company.company_name
    body = 'Hi,<br/><br/> ' \
           'System failed to Precharge customer %s`s card ending %s for monthly' \
           ' refill of $%s. <a href="%s">Charge</a>.<br/><br/>' \
           'Regards,<br/> %s' % \
           (
               charge.customer,
               charge.creditcard[-4:],
               charge.amount,
               '%s%s' % (settings.SITE_DOMAIN, reverse('charge_detail', args=[charge.id])),
               charge.company.company_name
           )
    notification = Notification.objects.create(
        company=CompanyProfile.objects.get(superuser_profile=True),
        email=charge.company.email_id,
        subject=subject,
        body=body,
        send_with=Notification.MAIL)
    try:
        notification.send_notification()
        charge.add_charge_step('precharge', Charge.SUCCESS,
                               'Send notification with error to user')
    except Exception, e:
        charge.add_charge_step('precharge', Charge.ERROR,
                               'Send notification with error to user failed '
                               'with error: "%s"' % e)


def failed_prerefill_company_notification(transaction):
    if transaction.current_step == 'check_payment':
        return
    subject = "[%s] PreRefill Error[%s]" % (transaction.company.company_name, transaction.current_step)
    body = '''System failed for customer %s's phone %s with plan %s.
            <a href="%s">Transaction</a>.<br/><br/>
            Regards, %s''' % \
           (transaction.customer.first_name,
            transaction.autorefill.phone_number,
            transaction.autorefill.plan,
            transaction.get_full_url(),
            transaction.company.company_name
            )
    notification = Notification.objects.create(
        company=CompanyProfile.objects.get(superuser_profile=True),
        customer=transaction.customer,
        email=transaction.company.email_id,
        phone_number=transaction.autorefill.phone_number,
        subject=subject,
        body=body,
        send_with=Notification.MAIL)
    try:
        notification.send_notification()
        transaction.add_transaction_step('prerefill error', 'send user mail', 'S', 'Message was sent to the user')
    except Exception, e:
        transaction.add_transaction_step('prerefill error', 'send user mail', 'E', 'Message wasn`t sent to the user, because:"%s"' % e)


def successful_prerefill_customer_notification(transaction):
    if transaction.customer.send_pin_prerefill != Customer.DONT_SEND\
            and transaction.company.able_change_send_pin_prerefill:
        subject = "[%s] Get Pin ended successfully" % transaction.company.company_name
        body = 'Hi %s,<br/><br/>' \
               'Tomorrow we are going to refill %s with plan %s $%s. ' \
               'Here is the refill pin %s , in case of any issues you can use ' \
               'this pin to call your carrier and refill your phone. ' \
               'Thanks for being a loyal customer.' \
               '<br/><br/>Regards, %s' % \
               (
                   transaction.customer,
                   transaction.autorefill.phone_number,
                   transaction.autorefill.plan.carrier.name,
                   transaction.autorefill.plan.plan_cost,
                   transaction.pin,
                   transaction.company.company_name
               )
        notification = Notification.objects.create(
            company=transaction.company,
            customer=transaction.customer,
            email=transaction.customer.primary_email,
            phone_number=transaction.autorefill.phone_number,
            subject=subject,
            body=body,
            send_with=transaction.customer.send_pin_prerefill)
        try:
            notification.send_notification()
            transaction.add_transaction_step('exist pin', 'send to the customer', 'S', 'Message was sent to the customer')
        except Exception, e:
            transaction.add_transaction_step('exist pin', 'send to the customer', 'E', 'Message wasn`t sent to the customer, because:"%s"' % e)


def failed_check_refunds_customer_notification(charge):
    subject = "Credit Card refund failed"
    body = 'Hi %s,\n\nOur refund for %s in amount %s failed please ' \
           'double check your getaway if it was refunded if not refund manual.' \
           '\n\nRegards,\n%s' % \
           (charge.customer.first_name,
            charge.creditcard[-4:],
            charge.amount,
            charge.company.company_name)
    Notification.objects.create(
        company=charge.company,
        customer=charge.customer,
        email=charge.customer.primary_email,
        phone_number=charge.autorefill.phone_number,
        subject=subject,
        body=body,
        send_with=Notification.MAIL)


def prepayment_customer_notification(autorefill, need_paid):
    subject = "[%s]Payments" % autorefill.company.company_name
    body = 'Hi %s,<br/><br/>Your phone number %s  is about to expire on %s. ' \
           'Please bring in $%s so that we can refill your phone .' \
           '<br/><br/>Regards, %s' % \
           (autorefill.customer.first_name,
            autorefill.phone_number,
            autorefill.renewal_date,
            need_paid,
            autorefill.company.company_name)
    Notification.objects.create(
        company=autorefill.company,
        customer=autorefill.customer,
        email=autorefill.customer.primary_email,
        phone_number=autorefill.phone_number,
        subject=subject,
        body=body,
        send_with=Notification.MAIL)