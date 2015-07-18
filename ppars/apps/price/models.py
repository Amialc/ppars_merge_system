from django.db import models
from django.core.urlresolvers import reverse
from django.db.models.signals import pre_save, post_save, pre_delete
from django.dispatch import receiver
from gadjo.requestprovider.signals import get_request
from ppars.apps.core import fields
from ppars.apps.core.models import Plan, Carrier, CompanyProfile, Log


class SellingPriceLevel(models.Model):
    level = models.CharField(max_length=1)
    created = models.DateTimeField('Created at', auto_now_add=True)
    updated = models.DateTimeField(verbose_name='Updated at', auto_now=True)

    def __unicode__(self):
        return u'%s level price' % self.level


class PlanSellingPrice(models.Model):
    carrier = fields.BigForeignKey(Carrier)
    plan = fields.BigForeignKey(Plan)
    company = fields.BigForeignKey(CompanyProfile)
    price_level = models.ForeignKey(SellingPriceLevel)
    selling_price = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    created = models.DateTimeField('Created at', auto_now_add=True)
    updated = models.DateTimeField(verbose_name='Updated at', auto_now=True)

    def __unicode__(self):
        return u'%s level price for %s' % (self.price_level.level, self.plan.plan_id)

    def get_absolute_url(self):
        return reverse('plan_selling_price_list')


def level_price_default():
    return SellingPriceLevel.objects.get(level='1')
