import logging
import traceback
from ppars.apps.core.models import CompanyProfile
from ppars.apps.core.send_notifications import send_notification
from ppars.apps.notification.models import Notification

logger = logging.getLogger('ppars')


def send_pin_error_mail(transaction, error_message):
    if transaction.company.pin_error:
        super_company = CompanyProfile.objects.get(superuser_profile=True)
        subject = "[%s] Pin Error in transaction %s" % (transaction.company.company_name, transaction.id)
        pin = ''
        if transaction.pin:
            pin = ' with pin %s' % transaction.pin
        body = '''Hi %s,<br/><br/>
            System failed for customer %s's phone %s with plan %s%s.
            <a href=\"%s\">Transaction</a> errored in step %s due to \"%s\".
            <br/><br/>Regards, %s ''' % \
               (
                   transaction.user,
                   transaction.customer,
                   transaction.autorefill.phone_number,
                   transaction.autorefill.plan,
                   pin,
                   transaction.get_full_url(),
                   transaction.current_step,
                   error_message,
                   super_company.company_name,
               )
        try:
            send_notification(transaction,
                              super_company,
                              subject,
                              body,
                              transaction.company.email_id,
                              Notification.MAIL)
        except Exception, e:
            logger.error("Exception: %s. Trace: %s." % (e, traceback.format_exc(limit=10)))