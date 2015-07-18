import json
from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.signals import pre_save, post_save, pre_delete
from django.dispatch import receiver
import requests
from twilio.rest import TwilioRestClient
from ppars.apps.core import fields
from ppars.apps.core.models import CompanyProfile, Customer, Log, UserProfile

from gadjo.requestprovider.signals import get_request


class Notification(models.Model):
    MAIL = 'EM'
    SMS = 'SM'
    SMS_EMAIL = 'SE'
    BOTH = 'B'

    SUCCESS = 'S'
    ERROR = 'E'

    SEND_TYPE_CHOICES = (
        (MAIL, 'Email'),
        (SMS, 'Sms'),
        (SMS_EMAIL, 'Sms via email'),
        (BOTH, 'Sms and email'),
    )

    STATUS_TYPE_CHOICES = (
        (SUCCESS, 'Success'),
        (ERROR, 'Error'),
    )

    company = fields.BigForeignKey(CompanyProfile)
    customer = fields.BigForeignKey(Customer, null=True)

    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=10, blank=True)
    subject = models.TextField()
    body = models.TextField()
    send_with = models.CharField(max_length=2, choices=SEND_TYPE_CHOICES, null=True)
    status = models.CharField(max_length=3, choices=STATUS_TYPE_CHOICES, null=True)
    adv_status = models.TextField()
    created = models.DateTimeField('Created at', auto_now_add=True)

    def __unicode__(self):
        return u'%s' % self.subject

    def send_twilio_sms(self, twilio_sid, twilio_auth_token, twilio_number):
        if not settings.TEST_MODE:
            client = TwilioRestClient(twilio_sid, twilio_auth_token)
            client.messages.create(from_="+1%s" % twilio_number,
                                   to="+1%s" % self.phone_number,
                                   body=self.body.replace('<br/>', '\n'))

    def send_mandrill_email(self, mandrill_key, mandrill_email, body=None):
        if not body:
            body = self.body
        form_fields = {
            "key": mandrill_key,
            "message": {
                "html": body,
                "subject": self.subject,
                "from_email": mandrill_email,
                "to": [{
                    "email": self.email,
                    "type": "to",
                }],
            }
        }
        if not settings.TEST_MODE:
            result = requests.post('https://mandrillapp.com/api/1.0/messages/send.json',
                                   data=json.dumps(form_fields),
                                   headers={'Content-Type': 'application/json'})
            return result

    def send_sms_email(self, sms_email, mandrill_key, mandrill_email):
        self.email = sms_email
        self.subject = ''
        self.body = self.body.replace('<br/>', ' ')
        self.save()
        separator = 140
        for part in [self.body[i:i+separator] for i in range(0, len(self.body), separator)]:
            self.send_mandrill_email(mandrill_key, mandrill_email, body=part)

    def send_notification(self):
        try:
            if self.MAIL == self.send_with or self.BOTH == self.send_with:
                self.send_mandrill_email(self.company.mandrill_key,
                                         self.company.mandrill_email,)
            elif self.SMS_EMAIL == self.send_with or self.BOTH == self.send_with:
                for phone_number in self.customer.sms_email.split(','):
                    self.send_sms_email(phone_number+'@'+self.customer.sms_gateway.gateway,
                                        self.company.mandrill_key,
                                        self.company.mandrill_email, )
            elif self.SMS == self.send_with or self.BOTH == self.send_with:
                self.body = '%s\nPlease, do not reply' % self.body
                self.send_twilio_sms(self.company.twilio_sid,
                                     self.company.twilio_auth_token,
                                     self.company.twilio_number)
            self.status = self.SUCCESS
            self.adv_status = 'Notification sent succesfully'
        except Exception, e:
            self.adv_status = 'Notification not sent because: "%s"' % e
            self.status = self.ERROR
            raise Exception(e)
        finally:
            self.save()


# for sending sms via email
class SmsEmailGateway(models.Model):
    name = models.CharField(max_length=50)
    gateway = models.CharField(max_length=50)

    def __unicode__(self):
        return '@%s(%s)' % (self.gateway, self.name)


class SpamMessage(models.Model):
    ALL = 'A'
    ENABLED = 'E'
    DISABLED = 'D'
    MAIL = 'EM'
    SMS = 'SM'
    SMS_EMAIL = 'SE'
    CUSTOMER_TYPE_CHOICES = (
        (ALL, 'All customers'),
        (ENABLED, 'Enabled customers'),
        (DISABLED, 'Disabled customers'),
    )
    SEND_TYPE_CHOICES = (
        (MAIL, 'Email'),
        (SMS, 'Sms'),
        (SMS_EMAIL, 'Sms via email'),
    )
    company = fields.BigForeignKey(CompanyProfile)
    message = models.CharField(max_length=500)
    send_with = models.CharField(max_length=2, choices=SEND_TYPE_CHOICES, default=SMS)
    customer_type = models.CharField(max_length=1, choices=CUSTOMER_TYPE_CHOICES, default=ALL)
    created = models.DateTimeField(auto_now_add=True)

    def __unicode__(self):
        return self.message

    def get_absolute_url(self):
        return reverse('sms_create')


class NewsMessage(models.Model):
    title = models.CharField(max_length=150, blank=True)
    message = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "News Message"

    def send_mandrill_email(self):
        for company in CompanyProfile.objects.filter(superuser_profile=False):
            if company.email_id:
                form_fields = {
                    "key": CompanyProfile.objects.get(superuser_profile=True).mandrill_key,
                    "message": {
                        "html": self.message,
                        "subject": self.title,
                        "from_email": CompanyProfile.objects.get(superuser_profile=True).mandrill_email,
                        "to": [{
                            "email": company.email_id,
                            "type": "to",
                        }],
                    }
                }
                if not settings.TEST_MODE:
                    result = requests.post('https://mandrillapp.com/api/1.0/messages/send.json',
                                           data=json.dumps(form_fields),
                                           headers={'Content-Type': 'application/json'})
            for user in UserProfile.objects.filter(company=company,
                                                   updates_email__isnull=False).exclude(updates_email=''):
                emails = [email.strip(' ') for email in user.updates_email.split(',') if email != '']
                for email in emails:
                    form_fields = {
                        "key": CompanyProfile.objects.get(superuser_profile=True).mandrill_key,
                        "message": {
                            "html": self.message,
                            "subject": self.title,
                            "from_email": CompanyProfile.objects.get(superuser_profile=True).mandrill_email,
                            "to": [{
                                "email": email,
                                "type": "to",
                            }],
                        }
                    }
                    if not settings.TEST_MODE:
                        result = requests.post('https://mandrillapp.com/api/1.0/messages/send.json',
                                               data=json.dumps(form_fields),
                                               headers={'Content-Type': 'application/json'})


class CustomPreChargeMessage(models.Model):
    company = fields.BigForeignKey(CompanyProfile)
    message = models.TextField(blank=True)
    use_message = models.BooleanField(default=False)

    def __unicode__(self):
        return self.message

    def get_absolute_url(self):
        return reverse('custom_message')


