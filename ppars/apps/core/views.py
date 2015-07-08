import uuid
import logging
import datetime
import json
import calendar
import csv
import traceback
from django.conf import settings
import pytz
import xlrd
import operator
import re
from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404
from django.template.defaultfilters import slugify
from pprint import pprint
from django.views.generic import ListView, DetailView
from django.views.generic.edit import View, CreateView, DeleteView, UpdateView
from django.utils import timezone as django_tz
from django.core.validators import validate_email
from django.forms import ValidationError
from django.db.models import Q
import cookielib
import mechanize
from lxml import etree
from pytz import timezone
import ext_lib
import forms
from itertools import chain
from models import Customer, AutoRefill, Transaction, TransactionStep, Plan, Carrier,\
    UnusedPin, CarrierAdmin,  PlanDiscount, CompanyProfile, CommandLog, \
    ConfirmDP, ImportLog, PinReport, Log, UserProfile, News, PhoneNumber
from ppars.apps.charge.models import Charge, TransactionCharge
from ppars.apps.accounts.forms import StrengthUserCreationForm, \
    PparsStrengthUserCreationForm
from tasks import queue_refill, queue_customer_import, queue_autorefill_import,\
    queue_import_customers_from_usaepay, \
    queue_compare_pins_with_dollarphone, queue_import_phone_numbers, \
    queue_prerefill

logger = logging.getLogger('ppars')


class Home(View):
    template_name = 'core/home.html'

    def get(self, request, *args, **kwargs):
        today = datetime.datetime.now(timezone('US/Eastern')).date()
        if request.user.is_superuser:
            refills = AutoRefill.objects.filter(enabled=True, renewal_date=today)
        else:
            refills = AutoRefill.objects.filter(company=request.user.profile.company, enabled=True, renewal_date=today)
        return render(request, self.template_name, {'autorefill_list': refills,
                                                    'sellingprices': request.user.profile.company.sellingprices_amount_for_week(),
                                                    'date_gmt': datetime.datetime.now(),
                                                    'last_updates': News.objects.order_by('-created')[:2],
                                                    'show_updates': request.user.profile.company.show_updates
                                                    })


class LogList(ListView):
    model = Log
    template_name = 'core/templates/ppars/log_list.html'
    context_object_name = 'log_list'

    def get_queryset(self):
        return self.request.user.profile.get_company_logs()


class CompanyProfileUpdate(View):
    model = CompanyProfile
    template_name = 'core/userprofile_form.html'
    form_class = forms.CompanyProfileForm

    def get(self, request, *args, **kwargs):
        form = forms.CompanyProfileForm(instance=request.user.profile.company)
        return render(request, self.template_name, {'form': form})

    def post(self, request, *args, **kwargs):
        form = forms.CompanyProfileForm(request.POST, instance=request.user.profile.company)
        if form.is_valid():
            p = self.request.user.profile
            if self.request.user.is_superuser:
                    form.instance.company_name = p.company.company_name
            form.instance.updated = True
            form.instance.superuser_profile = self.request.user.is_superuser
            form.save()
            messages.add_message(self.request, messages.SUCCESS, 'Company Profile updated successfully.')
            return render(request, self.template_name, {'form': form})
        else:
            logger.debug(form.errors)
            return render(request, self.template_name, {'form': form})


class CustomerList(ListView):
    model = Customer
    template_name = 'core/customer_list.html'

    def get_queryset(self):
        return self.request.user.profile.get_company_customers()

    def get(self, request, *args, **kwargs):
        return render(request,self.template_name, {'customers_allowed': UserProfile.objects.get(user=request.user).company.check_available_customer_create()})


class CustomerCreate(View):
    form_class = forms.CustomerForm
    model = Customer
    template_name = 'core/customer_form.html'

    def get(self, request, *args, **kwargs):
        if not UserProfile.objects.get(user=request.user).company.check_available_customer_create():
            messages.error(request, 'Customer limit has been reached. Please contact administrator.')
            return HttpResponseRedirect('/customer')
        form = forms.CustomerForm()
        form.fields['send_pin_prerefill'].initial = request.user.profile.company.send_pin_prerefill
        return render(request, self.template_name,
                      {
                          'form': form,
                          'use_sellercloud': request.user.profile.company.use_sellercloud,
                      })

    def post(self, request, *args, **kwargs):
        if not UserProfile.objects.get(user=request.user).company.check_available_customer_create():
            messages.error(request, 'Customer limit has been reached. Please contact administrator.')
            return HttpResponseRedirect('/customer')
        form = forms.CustomerForm(request.POST, request=request)
        if request.is_ajax():
            if not form.is_valid():
                return HttpResponse(json.dumps({'status': 'error', 'errors': form.errors}))
        if form.is_valid():
            form.instance.user = request.user
            form.instance.company = request.user.profile.company
            if not form.instance.charge_getaway:
                if form.instance.company.cccharge_type and form.instance.charge_type == Customer.CREDITCARD:
                    form.instance.charge_getaway = form.instance.company.cccharge_type
                else:
                    form.instance.charge_getaway = Customer.CASH_PREPAYMENT
            form.save()
            customer = form.instance
            customer.set_phone_numbers(form.cleaned_data['phone_numbers'].split(','))
            # customer.set_sms_email_to_first_phone_number()
            if form.cleaned_data.get('local_card'):
                from ppars.apps.card.models import Card
                card = Card.objects.get(id=form.cleaned_data.get('local_card'))
                card.customer = form.instance
                card.save()
            if request.is_ajax():
                return HttpResponse(json.dumps({'status': 'success', 'id': customer.id}))
            if form.message:
                messages.add_message(request, messages.WARNING, '%s' % form.message)
            messages.add_message(
                request,
                messages.SUCCESS,
                'Customer <a href="%s">%s</a> created successfully.' %
                (request.build_absolute_uri(reverse('customer_update', args=[form.instance.id])), form.instance))
            return render(request, 'core/customer_list.html',
                          {
                              'customer_list': request.user.profile.get_company_customers(),
                              'customers_allowed': UserProfile.objects.get(user=request.user).company.check_available_customer_create(),
                          })
        else:
            logger.debug(form.errors) #printing errors
            return render(request, self.template_name,
                          {
                              'form': form,
                              'use_sellercloud': request.user.profile.company.use_sellercloud,
                          })


class CustomerUpdate(View):
    form_class = forms.CustomerForm
    model = Customer
    template_name = 'core/customer_form.html'

    def get(self, request, pk, *args, **kwargs):
        customer = get_object_or_404(Customer, pk=pk)
        form = forms.CustomerForm(instance=customer, request=request, initial={'phone_numbers': ",".join(PhoneNumber.objects.filter(customer=customer).values_list('number', flat=True)) })
        amount = 0
        for charge in Charge.objects.filter(used=False, customer=customer):
            amount += charge.amount
        return render(request, self.template_name,
                      {
                          'form': form,
                          'customer': customer,
                          'object': customer,
                          'use_sellercloud': customer.company.use_sellercloud,
                          'unused_charge_count': Charge.objects.filter(used=False, customer=customer).count(),
                          'unused_charge_amount': amount
                      })

    def post(self, request, pk, *args, **kwargs):
        customer = get_object_or_404(Customer, pk=pk)
        form = forms.CustomerForm(request.POST, instance=customer, request=request)
        if request.is_ajax():
            if not form.is_valid():
                return HttpResponse(json.dumps({'status': 'error', 'errors': form.errors}))
        if form.is_valid():
            self.object = customer = form.save(commit=True)
            if not customer.charge_getaway and customer.company.cccharge_type:
                customer.charge_getaway = customer.company.cccharge_type
                customer.save()
            if 'enabled' in form.changed_data and not form.instance.enabled:
                autorefills = AutoRefill.objects.filter(customer=form.instance)
                for autorefill in autorefills:
                        autorefill.enabled = False
                        autorefill.save()
            if form.cleaned_data.get('local_card'):
                from ppars.apps.card.models import Card
                card = Card.objects.get(id=form.cleaned_data.get('local_card'))
                card.customer = form.instance
                card.save()
            if request.is_ajax():
                return HttpResponse(json.dumps({'status': 'success', 'id': customer.id}))
            if form.message:
                messages.add_message(request, messages.WARNING, '%s' % form.message)
            messages.add_message(
                request,
                messages.SUCCESS,
                'Customer <a href="%s">%s</a> updated successfully.' %
                (request.build_absolute_uri(
                    reverse(
                        'customer_update',
                        args=[form.instance.id])),
                form.instance))
            return render(request, 'core/customer_list.html',
                          {
                              'customer_list': request.user.profile.get_company_customers(),
                              'customers_allowed': UserProfile.objects.get(user=request.user).company.check_available_customer_create(),
                          })
        else:
            return render(request, self.template_name,
                          {
                              'form': form,
                              'use_sellercloud': customer.company.use_sellercloud,
                              'request': request,
                          })


class CustomerExport(View):
    def get(self, request, *args, **kwargs):
        template = request.GET.get('template')
        addcc = request.GET.get('addcc')
        response = HttpResponse(mimetype='text/csv')
        writer = csv.writer(response)
        if addcc == 'true':
                filename = 'customer_addcc_import_template.csv'
        else:
                filename = 'customer_basic_import_template.csv'
        writer.writerow(['First Name',
                         'Middle Name',
                         'Last Name',
                         'Primary Email',
                         'Phone Numbers',
                         'SellerCloud Account ID',
                         'Address',
                         'City',
                         'State',
                         'Zip',
                         'Charge Type',
                         'Card Number',
                         'Authorize ID',
                         'USAePay customer ID',
                         'Customer Discount',
                         'Send Status',
                         'Email Success',
                         'Group SMS',
                         'Enabled',
                         ])
        if template != 'true':
                filename = 'customer_export_{d.month}_{d.day}_{d.year}.csv'.format(d=datetime.datetime.now())
                customers = Customer.objects.filter(company=self.request.user.profile.company)
                for customer in customers:
                    phone_numbers = ",".join([phone.number for phone in PhoneNumber.objects.filter(customer=customer)])
                    writer.writerow([
                        customer.first_name,
                        customer.middle_name,
                        customer.last_name,
                        customer.primary_email,
                        phone_numbers,
                        customer.sc_account,
                        customer.address,
                        customer.city,
                        customer.state,
                        customer.zip,
                        customer.get_charge_type_display(),
                        customer.creditcard,
                        customer.authorize_id,
                        customer.usaepay_customer_id,
                        customer.customer_discount,
                        customer.get_send_status_display(),
                        customer.email_success,
                        customer.group_sms,
                        customer.enabled
                    ])
        response['Content-Disposition'] = 'attachment;filename=%s' % filename
        return response


class CustomerImport(View):
    template_name = 'core/customer_import.html'
    form_class = forms.GenericImportForm

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {'form': form, 'confirm': False})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            if form.cleaned_data['confirm'] == 'False':
                try:
                    customers = ext_lib.import_csv(form.cleaned_data['file'])
                    if len(customers)+Customer.objects.filter(company=UserProfile.objects.get(user=request.user).company).count()>UserProfile.objects.get(user=request.user).company.customer_limit and UserProfile.objects.get(user=request.user).company.customer_limit != 0:
                        messages.error(request, 'Customer limit has been reached. Please contact administrator.')
                        return HttpResponseRedirect('/customer')
                    cache_id = str(uuid.uuid1())
                    cache.add(key=cache_id, value={'customers': customers, 'user': request.user}, timeout=600)
                    return render(request, self.template_name,
                                  {'form': form, 'confirm': True, 'customers': customers, 'cache_id': cache_id})
                except Exception, e:
                    messages.add_message(self.request, messages.ERROR,
                                         'Failed to read import file, '
                                         'please ensure it is a csv and that it follows the template.')
                    logger.error(traceback.format_exc())
                    return HttpResponseRedirect(reverse('customer_list'))
            else:
                cache_data = cache.get(form.cleaned_data['cache_id'])
                queue_customer_import.delay(cache_data)
                messages.add_message(self.request, messages.SUCCESS,
                                     'Customer import job has been added to queue, results will be mailed to you.')
                return HttpResponseRedirect(reverse('customer_list'))
        else:
            return render(request, self.template_name, {'form': form, 'confirm': False})


class CustomerDelete(DeleteView):
    model = Customer

    def get_success_url(self):
        return reverse('customer_list')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        messages.add_message(self.request, messages.ERROR, 'Customer "%s" deleted successfully.' % self.object)
        return HttpResponseRedirect(self.get_success_url())


class PlanList(ListView):
    model = Plan


class PlanCreate(View):
    model = Plan
    form_class = forms.PlanForm
    template_name = 'core/plan_form.html'

    def get(self, request, *args, **kwargs):
        form = forms.PlanForm()
        up = Plan.objects.filter(universal=True)
        return render(request, self.template_name,
                      {
                          'form': form,
                          'up': up,
                      })

    def post(self, request, *args, **kwargs):
        form = forms.PlanForm(request.POST)
        up = Plan.objects.filter(universal=True)
        if form.is_valid():
            self.object = plan = form.save(commit=False)
            if plan.universal:
                plan.universal_plan = None
            plan.save()
            messages.add_message(self.request, messages.SUCCESS, 'Plan "%s" created successfully.' % form.instance)
            return render(request, 'core/plan_list.html',
                          {
                              'plan_list': Plan.objects.all(),
                          })
        else:
            logger.debug(form.errors)
            return render(request, self.template_name,
                          {
                              'form': form,
                              'up': up,
                          })


class PlanUpdate(View):
    model = Plan
    form_class = forms.PlanForm
    template_name = 'core/plan_form.html'

    def get(self, request, pk, *args, **kwargs):
        plan = get_object_or_404(Plan, pk=pk)
        up = Plan.objects.exclude(pk=pk).filter(universal=True)
        form = forms.PlanForm(instance=plan)
        return render(request, self.template_name,
                      {
                          'form': form,
                          'up': up,
                      })

    def post(self, request, pk, *args, **kwargs):
        plan = get_object_or_404(Plan, pk=pk)
        form = forms.PlanForm(request.POST, instance=plan)
        up = Plan.objects.exclude(pk=pk).filter(universal=True)
        if form.is_valid():
            self.object = plan = form.save(commit=False)
            if plan.universal:
                plan.universal_plan = None
            plan.save()
            messages.add_message(self.request, messages.SUCCESS, 'Plan "%s" updated successfully.' % form.instance)
            return render(request, 'core/plan_list.html',
                          {
                              'plan_list':  Plan.objects.all(),
                          })
        else:
            return render(request, self.template_name,
                          {
                              'form': form,
                              'up': up,
                          })


class PlanDelete(DeleteView):
    model = Plan

    def get_success_url(self):
        return reverse('plan_list')


class PlanExport(View):

    def get(self, request, *args, **kwargs):
        template = request.GET.get('template')
        response = HttpResponse(mimetype='text/csv')
        filename = 'plans_import_template.csv'
        writer = csv.writer(response)
        writer.writerow(['Plan ID', 'SC_SKU', 'API ID', 'Carrier', 'Plan Name', 'Plan Cost', 'Plan Type', 'Available', 'Universal', 'Universal Plan'])
        if template != 'true':
            filename = 'plan_export_{d.month}_{d.day}_{d.year}.csv'.format(d=datetime.datetime.now())
            plans = Plan.objects.all().order_by('-universal')
            for plan in plans:
                writer.writerow([plan.plan_id, plan.sc_sku, plan.api_id, plan.carrier, plan.plan_name, plan.plan_cost, plan.get_plan_type_display(), plan.available, plan.universal, plan.universal_plan])
        response['Content-Disposition'] = 'attachment;filename=%s' % filename
        return response


class PlanImport(View):
    template_name = 'core/plan_import.html'
    form_class = forms.GenericImportForm

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {'form': form, 'confirm': False})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            if form.cleaned_data['confirm'] == 'False':
                try:
                    plans = ext_lib.import_csv(form.cleaned_data['file'])
                    cache_id = str(uuid.uuid1())
                    cache.add(key=cache_id, value={'plans': plans}, timeout=600)
                    return render(request, self.template_name,
                                  {'form': form,
                                   'confirm': True,
                                   'plans': plans,
                                   'cache_id': cache_id})
                except Exception, e:
                    messages.add_message(self.request, messages.ERROR, 'Failed to read import file, please ensure it is a csv and that it follows the template.')
                    logger.error(traceback.format_exc())
                    return HttpResponseRedirect(reverse('plan_list'))
            else:
                try:
                    cache_data = cache.get(form.cleaned_data['cache_id'])
                    plan_types = dict()
                    universal_plans = dict()
                    for ptype in Plan.PLAN_TYPE_CHOICES:
                        plan_types[ptype[1]] = ptype[0]
                    for plan in cache_data['plans']:
                        plan['carrier'] = Carrier.objects.get(name=plan['carrier'])
                        plan['plan_type'] = plan_types[plan['plan_type']]
                        if plan['available'] == 'False':
                            plan['available'] = False
                        else:
                            plan['available'] = True
                        if plan['universal'] == 'True':
                            plan['universal'] = True
                        else:
                            plan['universal'] = False
                            if plan['universal_plan']:
                                plan['universal_plan'] = universal_plans[plan['universal_plan']]

                        if Plan.objects.filter(plan_id=plan['plan_id']).exists():
                            logger.debug('plan %s' % plan['plan_id'])
                            this_plan = Plan.objects.get(plan_id=plan['plan_id'])
                            for prop in plan:
                                setattr(this_plan, prop, plan[prop])
                        else:
                            this_plan = Plan(**plan)
                        if plan['universal'] == True:
                            universal_plans[plan['plan_id']] = this_plan
                        this_plan.save()
                    messages.add_message(self.request, messages.SUCCESS, 'Plans imported successfully.')
                except Exception, e:
                    messages.add_message(self.request, messages.ERROR, 'Plan imported failed, please contact the administrator for furthur information')
                    logger.error(traceback.format_exc())
                finally:
                    return HttpResponseRedirect(reverse('plan_list'))
        else:
            return render(request, self.template_name, {'form': form, 'confirm': False})


class PlanDiscountList(ListView):
    model = PlanDiscount

    def get_queryset(self):
        return PlanDiscount.objects.filter(user=self.request.user)


class PlanDiscountCreate(CreateView):
    form_class = forms.PlanDiscountForm
    model = PlanDiscount

    def get_form_kwargs(self):
        kwargs = super(PlanDiscountCreate, self).get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.company = self.request.user.profile.company
        return super(PlanDiscountCreate, self).form_valid(form)


class PlanDiscountUpdate(UpdateView):
    form_class = forms.PlanDiscountForm
    model = PlanDiscount

    def get_form_kwargs(self):
        kwargs = super(PlanDiscountUpdate, self).get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs


class CarrierList(ListView):
    model = Carrier


class CarrierCreate(CreateView):
    model = Carrier
    form_class = forms.CarrierForm

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.company = self.request.user.profile.company
        return super(CarrierCreate, self).form_valid(form)


class CarrierUpdate(UpdateView):
    model = Carrier
    form_class = forms.CarrierForm


class CarrierDelete(DeleteView):
    model = Carrier

    def get_success_url(self):
        return reverse('carrier_list')


class CarrierExport(View):

    def get(self, request, *args, **kwargs):
        template = request.GET.get('template')
        response = HttpResponse(mimetype='text/csv')
        filename = 'carriers_import_template.csv'
        writer = csv.writer(response)
        writer.writerow(['Name', 'Recharge Number', 'Admin Site', 'Renew Days', 'Renew Months'])
        if template != 'true':
            filename = 'carrier_export_{d.month}_{d.day}_{d.year}.csv'.format(d=datetime.datetime.now())
            carriers = Carrier.objects.all()
            for carrier in carriers:
                writer.writerow([carrier.name, carrier.recharge_number, carrier.admin_site, carrier.renew_days, carrier.renew_months])
        response['Content-Disposition'] = 'attachment;filename=%s' % filename
        return response


class CarrierImport(View):
    template_name = 'core/carrier_import.html'
    form_class = forms.GenericImportForm

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {'form': form, 'confirm': False})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            if form.cleaned_data['confirm'] == 'False':
                try:
                    carriers = ext_lib.import_csv(form.cleaned_data['file'])
                    cache_id = str(uuid.uuid1())
                    cache.add(key=cache_id, value={'carriers': carriers}, timeout=600)
                    return render(request, self.template_name,
                                  {'form': form,
                                   'confirm': True,
                                   'carriers': carriers,
                                   'cache_id': cache_id})
                except Exception, e:
                    messages.add_message(self.request, messages.ERROR, 'Failed to read import file, please ensure it is a csv and that it follows the template.')
                    logger.error(traceback.format_exc())
                    return HttpResponseRedirect(reverse('carrier_list'))
            else:
                try:
                    cache_data = cache.get(str(form.cleaned_data['cache_id']))
                    for carrier in cache_data['carriers']:
                        if Carrier.objects.filter(name=carrier['name']).exists():
                            this_carrier = Carrier.objects.get(name=carrier['name'])
                            for prop in carrier:
                                setattr(this_carrier, prop, carrier[prop])
                        else:
                            this_carrier = Carrier(**carrier)
                        this_carrier.save()
                    messages.add_message(self.request, messages.SUCCESS, 'Carriers imported successfully.')
                except Exception, e:
                    messages.add_message(self.request, messages.ERROR, 'Carrier imported failed, please contact the administrator for furthur information')
                    logger.error(traceback.format_exc())
                finally:
                    return HttpResponseRedirect(reverse('carrier_list'))
        else:
            return render(request, self.template_name, {'form': form, 'confirm': False})


class CarrierAdminList(ListView):
    model = CarrierAdmin

    def get_queryset(self):
        return CarrierAdmin.objects.filter(company_id=self.request.user.profile.company_id)


class CarrierAdminCreate(CreateView):
    form_class = forms.CarrierAdminForm
    model = CarrierAdmin

    def get_form_kwargs(self):
        kwargs = super(CarrierAdminCreate, self).get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def form_valid(self, form):
        form.instance.company = self.request.user.profile.company
        return super(CarrierAdminCreate, self).form_valid(form)


class CarrierAdminUpdate(UpdateView):
    form_class = forms.CarrierAdminForm
    model = CarrierAdmin

    def get_form_kwargs(self):
        kwargs = super(CarrierAdminUpdate, self).get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs


class AutoRefillList(ListView):
    model = AutoRefill

    def get_queryset(self):
        return AutoRefill.objects.filter(trigger="SC", company=self.request.user.profile.company)


class AutoRefillCreate(CreateView):
    form_class = forms.AutoRefillForm
    model = AutoRefill

    def get_form_kwargs(self):
        kwargs = super(AutoRefillCreate, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_initial(self):
        data = {}
        if self.request.GET.get('cid'):
            data['customer'] = Customer.objects.get(id=self.request.GET['cid'])
        if self.request.GET.get('ph'):
            data['phone_number'] = self.request.GET['ph']
        return data

    def form_valid(self, form):
        self.object = auto_refill = form.save(commit=False)
        auto_refill.user = self.request.user
        auto_refill.company = self.request.user.profile.company
        auto_refill.trigger = "SC"
        auto_refill.refill_type = "FR"
        return super(AutoRefillCreate, self).form_valid(form)


class AutoRefillUpdate(UpdateView):
    form_class = forms.AutoRefillForm
    model = AutoRefill

    def get_form_kwargs(self):
        kwargs = super(AutoRefillUpdate, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs


class AutoRefillDelete(DeleteView):
    model = AutoRefill

    def get_success_url(self):
        return reverse('autorefill_list')


class AutoRefillExport(View):
    def get(self, request, *args, **kwargs):
        template = request.GET.get('template')
        response = HttpResponse(mimetype='text/csv')
        filename = 'scheduledrefills_import_template.csv'
        writer = csv.writer(response)
        writer.writerow(['Customer',
                         'Phone Number',
                         'Plan',
                         'Renewal Date',
                         'Renewal End Date',
                         'Renewal Interval',
                         'Schedule',
                         'Notes',
                         'Enabled'])
        if template != 'true':
            filename = 'scheduledrefills_export_{d.month}_{d.day}_{d.year}.csv'.format(d=datetime.datetime.now())
            autorefills = AutoRefill.objects.filter(user=self.request.user, trigger="SC")
            for autorefill in autorefills:
                writer.writerow([
                    autorefill.customer,
                    autorefill.phone_number,
                    autorefill.plan, autorefill.renewal_date,
                    autorefill.renewal_end_date,
                    autorefill.renewal_interval,
                    autorefill.get_schedule_display(),
                    autorefill.notes,
                    autorefill.enabled
                ])
        response['Content-Disposition'] = 'attachment;filename=%s' % filename
        return response


class AutoRefillImport(View):
    template_name = 'core/autorefill_import.html'
    form_class = forms.GenericImportForm

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {'form': form, 'confirm': False})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            if form.cleaned_data['confirm'] == 'False':
                try:
                    autorefills = ext_lib.import_csv(form.cleaned_data['file'])
                    checked_autorefills = []
                    for autorefill in autorefills:
                        autorefill['plan'] = Plan.objects.get(plan_id=autorefill['plan'])
                        autorefill['status'] = 'F'
                        # Verification for duplicated refills in imported file
                        if '%s-%s' % (autorefill['plan'], autorefill['phone_number']) in checked_autorefills:
                            autorefill['result'] = 'Autorefill already imported'
                        else:
                            customers = []
                            for customer in Customer.objects.filter(company=request.user.profile.company):
                                if PhoneNumber.objects.filter(company=request.user.profile.company, numbers__contains=autorefill['phone_number']):
                                    customers.append(customer)
                            # Verification for duplicated refills in system
                            if request.user.profile.company.block_duplicate_schedule:
                                ars = AutoRefill.objects.filter(
                                    trigger='SC',
                                    enabled=True,
                                    plan=autorefill['plan'],
                                    phone_number__contains=autorefill['phone_number'],
                                    company=request.user.profile.company)
                                if ars.count() > 0:
                                    message = []
                                    for ar in ars:
                                        message.append('<a href="%s">%s</a>' % (reverse('autorefill_update', args=[ar.id]), ar))
                                        autorefill['customer'] = ar.customer
                                    autorefill['result'] = 'Duplicated refills %s' % (', '.join(message))
                            # Searching customer by phone number
                            if 'result' not in autorefill:
                                if customers.count() > 1:
                                    autorefill['result'] = 'More than 1 customer has number "%s"' % autorefill['phone_number']
                                elif customers.count() < 1:
                                    autorefill['result'] = 'Nobody has number "%s"' % autorefill['phone_number']
                                else:
                                    autorefill['customer'] = customers[0]
                                    autorefill['result'] = 'Autorefill will be added'
                                    autorefill['status'] = 'S'
                                    checked_autorefills.append('%s-%s' % (autorefill['plan'], autorefill['phone_number']))
                            if autorefill['renewal_end_date'] and not re.findall(r'(19|20)\d\d-((0[1-9]|1[012])-(0[1-9]|[12]\d)|(0[13-9]|1[012])-30|(0[13578]|1[02])-31)', autorefill['renewal_end_date']):
                                autorefill['result'] = '%s "%s" value has an invalid date format. It must be in YYYY-MM-DD format.' % (autorefill['result'], autorefill['renewal_end_date'])
                                autorefill['renewal_end_date'] = None
                    cache_id = str(uuid.uuid1())
                    cache.add(key=cache_id, value={'autorefills': autorefills, 'user': request.user}, timeout=600)
                    return render(request, self.template_name,
                                  {
                                      'form': form, 'confirm': True,
                                      'autorefills': autorefills,
                                      'cache_id': cache_id
                                  })
                except Exception, e:
                    messages.add_message(request, messages.ERROR,
                                         'Failed to read import file, '
                                         'please ensure it is a csv and '
                                         'that it follows the template.')
                    logger.error(traceback.format_exc())
                    return HttpResponseRedirect(reverse('autorefill_list'))
            else:
                cache_data = cache.get(form.cleaned_data['cache_id'])
                queue_autorefill_import.delay(cache_data)
                messages.add_message(request, messages.SUCCESS,
                                     'Scheduled Refill import job has been '
                                     'added to queue, results will be mailed '
                                     'to you.')
                return HttpResponseRedirect(reverse('autorefill_list'))
        else:
            return render(request, self.template_name, {'form': form, 'confirm': False})


class ManualRefill(View):
    form_class = forms.ManualRefillForm
    template_name = 'core/manualrefill.html'

    def get(self, request, *args, **kwargs):
        init = dict()
        if request.GET.get('cid'):
            init['customer'] = Customer.objects.get(id=request.GET['cid'])
        if request.GET.get('ph'):
            init['phone_number'] = request.GET['ph']
        form = self.form_class(request.user, initial=init)
        return render(request, self.template_name, {'form': form})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.user, data=request.POST)
        if form.is_valid():
            self.object = manual_refill = form.save(commit=False)
            manual_refill.company = request.user.profile.company
            manual_refill.user = self.request.user
            manual_refill.trigger = "MN"
            manual_refill.save()
            transaction = Transaction(
                user=request.user,
                autorefill=manual_refill,
                state="Q",
                pin=manual_refill.pin,
                company=request.user.profile.company,
                triggered_by=request.user.username
            )
            transaction.save()

            if form.data['created-from']:

                trans_id_from = int(form.data['created-from'])

                transaction_from = Transaction.objects.get(id=trans_id_from)
                transaction_from.add_transaction_step('create similar',
                                                      'Created similar transaction',
                                                      TransactionStep.SUCCESS,
                                                      'Created similar transaction <a href="%s">%s</a> by user %s' %
                                                      (reverse('transaction_detail',
                                                               args=[transaction.id]),
                                                       transaction.id,
                                                       request.user))

                transaction_from.save()
                transaction.add_transaction_step('create',
                                                 'Created from existing transaction',
                                                 TransactionStep.SUCCESS,
                                                 'Created from transaction <a href="%s">%s</a> by user %s' %
                                                 (reverse('transaction_detail',
                                                          args=[trans_id_from]),
                                                  trans_id_from,
                                                  request.user))
                transaction.save()

            if form.data["datetime_refill"] != "":
                time_format = "%d %B %Y %H:%M"
                time = datetime.datetime.strptime(form.data["datetime_refill"], time_format)
                eta = time - datetime.timedelta(minutes=int(form.data["datetime_refill_tzone"]))
                if eta > datetime.datetime.now():
                    queue_refill.apply_async(args=[transaction.id], eta=eta)
                    transaction.adv_status = 'Waiting of execution at ' + time.strftime(time_format)
                    transaction.status = Transaction.WAITING
                    transaction.save()
                    transaction.add_transaction_step('wait', 'Waiting of execution', TransactionStep.WAITING,
                                                     transaction.adv_status)
                else:
                    queue_refill.delay(transaction.id)
            else:
                queue_refill.delay(transaction.id)

            return HttpResponseRedirect(reverse('transaction_detail', args=[transaction.id]))
        else:
            return render(request, self.template_name, {'form': form})

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.trigger = "MN"
        return super(ManualRefill, self).form_valid(form)


class UnusedPinList(ListView):
    model = UnusedPin

    def get_queryset(self):
        return UnusedPin.objects.filter(company=self.request.user.profile.company)


class UnusedPinCreate(CreateView):
    form_class = forms.UnusedPinForm
    model = UnusedPin

    def get_initial(self):
        data = {}
        if self.request.GET.get('pin'):
            data['pin'] = self.request.GET['pin']
        if self.request.GET.get('plan'):
            data['plan'] = Plan.objects.get(id=self.request.GET['plan'])
        return data

    def get_context_data(self, **kwargs):
        context = super(UnusedPinCreate, self).get_context_data(**kwargs)
        if self.request.GET.get('plan'):
            context['plan'] = Plan.objects.get(id=self.request.GET['plan'])
        return context

    def form_valid(self, form):
        self.object = unusedpin = form.save(commit=False)
        unusedpin.user = self.request.user
        unusedpin.company = self.request.user.profile.company
        for charge in Charge.objects.filter(pin=unusedpin.pin, pin_used=False):
            charge.pin_used = True
            charge.save()
            unusedpin.notes = "%s %s" % (unusedpin.notes, charge.get_full_url())
        return super(UnusedPinCreate, self).form_valid(form)


class UnusedPinUpdate(UpdateView):
    form_class = forms.UnusedPinForm
    model = UnusedPin


class UnusedPinDelete(DeleteView):
    model = UnusedPin

    def get_success_url(self):
        return reverse('unusedpin_list')


class UnusedPinImport(View):
    template_name = 'core/unusedpin_import.html'
    form_class = forms.UnusedPinImportForm

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {'form': form, 'confirm' : False})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            if form.cleaned_data['confirm'] == 'False':
                try:
                    pins = ext_lib.import_csv(form.cleaned_data['file'])
                    cache_id = str(uuid.uuid1())
                    plan = Plan.objects.get(id=form.cleaned_data['plan'])
                    cache.add(key=cache_id, value={'plan' : plan, 'pins': pins, 'notes': form.cleaned_data['notes']}, timeout=600)
                    return render(request, self.template_name, {'form': form, 'confirm': True, 'plan': plan, 'pins': pins, 'cache_id': cache_id})
                except Exception, e:
                    messages.add_message(self.request, messages.ERROR, 'Failed to read import file, please ensure it is a csv and that it follows the template.')
                    logger.error(traceback.format_exc())
                    return HttpResponseRedirect(reverse('unusedpin_list'))

            else:
                try:
                    cache_data = cache.get(form.cleaned_data['cache_id'])
                    for pin in cache_data['pins']:
                        unusedpin = UnusedPin(user=request.user, company=request.user.profile.company, plan=cache_data['plan'], pin=pin['pin'], used=False, notes=cache_data['notes'])
                        unusedpin.save()
                    messages.add_message(request, messages.SUCCESS, 'Successfully imported pins for plan "%s".'%cache_data['plan'])
                except Exception, e:
                    messages.add_message(self.request, messages.ERROR, 'Pin import failed, please contact the administrator for furthur information')
                    logger.error(traceback.format_exc())
                finally:
                    return HttpResponseRedirect(reverse('unusedpin_list'))
        else:
            return render(request, self.template_name, {'form': form, 'confirm': False})


class TransactionList(ListView):
    model = Transaction

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Transaction.objects.all()
        else:
            return Transaction.objects.filter(company=self.request.user.profile.company).order_by('ended')

    def get_context_data(self, **kwargs):
        context = super(TransactionList, self).get_context_data(**kwargs)
        context['user_list'] = User.objects.filter(is_superuser=False)
        return context


class TransactionDetail(DetailView):
    model = Transaction

    def get_context_data(self, **kwargs):
        context = super(TransactionDetail, self).get_context_data(**kwargs)
        context['step_list'] = TransactionStep.objects.filter(transaction=kwargs['object'].id).order_by('created')
        return context


class ConfirmDPView(View):
    form_class = forms.ConfirmDPForm
    model = ConfirmDP
    template_name = 'core/confirm_dp.html'

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {'form': form, 'success': False})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST)
        if form.is_valid():
            if form.cleaned_data['success'] == 'False':
                try:
        #             login = form.cleaned_data['login']
        #             password = form.cleaned_data['password']
        #             payload = {'user_session[email]': login, 'user_session[password]': password, }
        #             s = requests.Session()
        #             r = s.post('https://www.dollarphonepinless.com/user_session', data=payload)
        #             payload = {'user_authentication[delivery_method]': "email:%s" % login}
        #             r = s.post('https://www.dollarphonepinless.com/user_authentication/authenticate', data=payload)
        #             messages.add_message(self.request, messages.SUCCESS, '%s %s' % (r.url, r.text))
        #             cache_id = str(uuid.uuid1())
        #             cache.add(key=cache_id, value={'session': s}, timeout=1200)
        #             return render(request, self.template_name,
        #                           {'form': form, 'success': True, 'cache_id': cache_id})
        #         except Exception, msg:
        #             messages.add_message(self.request, messages.ERROR, '%s %s' % (r.url, r.text))
        #             return render(request, self.template_name)
        #     else:
        #         cache_data = cache.get(form.cleaned_data['cache_id'])
        #         s = cache_data['session']
        #         payload = {'user_authentication[code]': form.cleaned_data['confirm']}
        #         r = s.post('https://www.dollarphonepinless.com/user_authentication/new', data=payload)
        #         messages.add_message(self.request, messages.ERROR, '%s %s' % (r.url, r.text))
        #         if r.url == 'https://www.dollarphonepinless.com/dashboard':
        #             messages.add_message("Dollarphone account verified successfully")
        #         else:
        #             messages.add_message(self.request, messages.ERROR, '%s %s' % (r.url, r.text))
        #         return render(request, self.template_name)
        # else:
        #     return render(request, self.template_name, {'form': form, 'success': False})
                    br = mechanize.Browser()
                    cj = cookielib.LWPCookieJar()
                    br.set_handle_equiv(True)
                    br.set_handle_redirect(True)
                    br.set_handle_referer(True)
                    br.set_handle_robots(False)
                    br.set_handle_refresh(mechanize._http.HTTPRefreshProcessor(), max_time=1)
                    br.addheaders = [('User-agent', 'Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.1) Gecko/2008071615 Fedora/3.0.1-1.fc9 Firefox/3.0.1')]
                    br.open('https://www.dollarphonepinless.com/sign-in')
                    br.select_form(nr=0)
                    br.form['user_session[email]'] = form.cleaned_data['login']
                    br.form['user_session[password]'] = form.cleaned_data['password']
                    br.submit()
                    # messages.add_message(self.request, messages.ERROR, '%s %s' % (br.geturl(), br.response().read()))
                    br.select_form(nr=0)
                    br.form['user_authentication[delivery_method]'] = ["email:%s" % form.cleaned_data['login']]
                    br.submit()
                    # messages.add_message(self.request, messages.ERROR, '%s %s' % (br.geturl(), br.response().read()))
                    cache_id = str(uuid.uuid1())
                    cache.add(key=cache_id, value={'session': br}, timeout=1200)
                    return render(request, self.template_name,
                                  {'form': form, 'success': True, 'cache_id': cache_id})
                except Exception, msg:
                    messages.add_message(self.request, messages.ERROR, '%s %s' % (br.geturl(), br.response().read()))
                    return render(request, self.template_name)
            else:
                cache_data = cache.get(form.cleaned_data['cache_id'])
                br = cache_data['session']
                # messages.add_message(self.request, messages.ERROR, '%s %s' % (br.geturl(), br.response().read()))
                br.select_form(nr=0)
                br.form['user_authentication[code]'] = form.cleaned_data['confirm']
                br.submit()
                # messages.add_message(self.request, messages.ERROR, '%s %s' % (br.geturl(), br.response().read()))
                if br.geturl() == 'https://www.dollarphonepinless.com/dashboard':
                    messages.add_message("Dollarphone account verified successfully")
                else:
                    messages.add_message(self.request, messages.ERROR, '%s %s' % (br.geturl(), br.response().read()))
                return render(request, self.template_name)
        else:
            return render(request, self.template_name, {'form': form, 'success': False})


class PinReportList(ListView):
    model = PinReport

    def get_queryset(self):
        return PinReport.objects.filter(company=self.request.user.profile.company)


class PinReportDetail(DetailView):
    model = PinReport


class NewsDetail(DetailView):
    model = News


def news(request):
    return render(request, 'core/news.html')


def ajax_news(request):
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [], 'iTotalRecords': 0}
    order = '-'
    if request.GET['sSortDir_0'] == 'desc':
        order = ''
    for news in News.objects.filter(title__icontains=request.GET['sSearch'],
                                    category__contains=request.GET['sSearch_0']).order_by(order + 'created'):
        ajax_response['iTotalRecords'] += 1
        ajax_response['aaData'].append(['<a href=\'%s' % (reverse('news_detail', args=[news.id])) +
                                        '\'>' + news.__unicode__() + '</a>',
                                        news.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y"
                                                                                                 " %I:%M:%S%p")])
    ajax_response['iTotalDisplayRecords'] = len(ajax_response['aaData'])
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    ajax_response['aaData'] = ajax_response['aaData'][start:start+length]
    json_data = json.dumps(ajax_response)
    return HttpResponse(json_data, content_type='application/json')


def search(request):
    if 'searchfor' in request.GET and request.GET['searchfor'] and '__' not in request.GET['searchfor']:
        search_string = re.sub('\D', '', request.GET['searchfor'])
        is_number = False
        is_phone_number = False
        if len(search_string.split(' ')) == 1 and search_string.isdigit():
            is_number = True
            if len(search_string) == 10:
                is_phone_number = True
        return render(request, 'core/search.html', {'searching_for': request.GET['searchfor'], 'is_number': is_number,
                                                    'number': search_string, 'is_phone_number': is_phone_number})
    else:
        return HttpResponseRedirect('/home')


def ajax_search_dropdown(request):
    filters = request.GET['text'].strip().split(' ')
    for i in range(len(filters)):
        if re.compile('\d').search(filters[i]):  # if filter word contains digits it's a phone number
            filters[i] = re.sub('\D', '', filters[i])  # so we remove all additional symbols for right filtering
        else:  # in case it's a name we also should remove all additional symbols
            just_number = False
            filters[i] = re.sub('[^a-zA-Z]', '', filters[i])
    numbers = []
    names = []
    for fil in filters:
        if fil.isdigit():
            numbers.append(fil)
        else:
            names.append(fil)
    customers = []
    filtered_customers = []
    if numbers:
        for phone in PhoneNumber.objects.filter(reduce(operator.and_, (Q(number__icontains=number) for number in numbers)),
                                                company=request.user.profile.company).exclude(customer=None):
            customers.append((phone.customer, phone.number))
    if not customers:
        if names:
            filtered_customers = [(customer, PhoneNumber.objects.filter(customer=customer)[0].number)
                                  for customer in Customer.objects.filter(reduce(operator.and_,
                                                                                 (Q(first_name__icontains=name) |
                                                                                  Q(middle_name__icontains=name) |
                                                                                  Q(last_name__icontains=name)
                                                                                  for name in names)),
                                                                          company=request.user.profile.company)]
    else:
        for customer in customers:
            append = True
            for name in names:
                if not name in customer[0].__unicode__():
                    append = False
                    break
            if append:
                filtered_customers.append((customer[0], customer[1]))
    if len(customers) > 29:
        filtered_customers = filtered_customers[:29]
    return HttpResponse(json.dumps([{'name': customer[0].__unicode__(), 'number': customer[1]}
                                    for customer in filtered_customers]),
                        content_type='application/json')


# crazy thing and it's still growing, soon will be separated in another module or even an app
def ajax_search(request):
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [], 'iTotalRecords': 0}
    filters = request.GET['search_for'].strip().split(' ')
    just_number = True
    names = []
    numbers = []
    for i in range(len(filters)):
        if re.compile('\d').search(filters[i]):  # if filter word contains digits it's a phone number
            filters[i] = re.sub('\D', '', filters[i])  # so we remove all additional symbols for right filtering
            numbers.append(filters[i])
        else:  # in case it's a name we also should remove all additional symbols
            just_number = False
            filters[i] = re.sub('[^a-zA-Z]', '', filters[i])
            names.append(filters[i])
    # check if filter words is correct phone number
    number = ''
    if just_number:
        for fil in filters:
            number += fil
        if not len(number) == 10:
            just_number = False
    for i in range(len(filters)):
        if re.compile('\d').search(filters[i]):  # if one of filter words contains digits it's a phone number
            filters[i] = re.sub('\D', '', filters[i])  # so we remove all additional symbols for right filtering
    # check if we are looking just by last 4 numbers of CC
    if request.GET['search_for'][0] == '*' or request.GET['sSearch_0'] == 'Last4ofCC':
        # filtering company's customers
        for customer in Customer.objects.filter(creditcard__endswith=filters[0], company=request.user.profile.company):
            unused_charges_count = ' Unused charges: <b>' + str(Charge.objects.filter(used=False, customer=customer).count()) + '</b>'
            unused_charges_amount = 0
            for charge in Charge.objects.filter(used=False, customer=customer):
                unused_charges_amount += charge.amount
            ajax_response['aaData'].append([type(customer).__name__,
                                                        '<a href=\'%s' % (reverse('customer_update', args=[customer.id])) +
                                                        '\'>' + customer.__unicode__() + '</a>' +
                                                        ' (<b>' + customer.get_charge_getaway_display() + '</b>)' +
                                                        unused_charges_count + ' (<b>$' + str(unused_charges_amount)
                                                        + '</b>)'])
        # filtering company's charges
        for charge in Charge.objects.filter(creditcard__endswith=filters[0], company=request.user.profile.company).order_by('-created'):
            ajax_response['iTotalRecords'] += 1
            cc_last_four = ''
            used = '</a><span class="fa fa-minus-circle text-danger"></span>'
            used_for = ' used for '
            if charge.creditcard:
                cc_last_four = ' *' + charge.creditcard[-4:]
            if charge.used:
                used = '</a><span class="fa fa-check-circle text-success"></span>'
            if charge.autorefill:
                if charge.autorefill.phone_number:
                    used_for += '<b>' + charge.autorefill.phone_number + '</b>'
            ajax_response['aaData'].append([type(charge).__name__,
                                            '<a href=\'%s' % (reverse('charge_detail', args=[charge.id])) +
                                            '\'>Credit Card Charge #' + charge.__unicode__() + cc_last_four + '</a> '
                                            + used + ' (<b>'
                                            + charge.get_status_display() + '</b>)' + used_for + ' Created: <b>' +
                                            charge.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y "
                                                                                                       "%I:%M:%S%p") +
                                            '</b>'])
        json_data = json.dumps(ajax_response)
        return HttpResponse(json_data, content_type='application/json')
    #regular search
    else:
        # filtering company's phone numbers
        for phone_number in PhoneNumber.objects.filter(reduce(operator.and_, (Q(customer__first_name__icontains=val) |
                                                                              Q(customer__middle_name__icontains=val) |
                                                                              Q(customer__last_name__icontains=val) |
                                                                              Q(number__icontains=val)
                                                                              for val in filters)),
                                                       company=request.user.profile.company).exclude(customer=None):
            ajax_response['iTotalRecords'] += 1
            if request.GET['sSearch_0'] == '' or request.GET['sSearch_0'] == 'All'\
                    or request.GET['sSearch_0'] == 'PhoneNumber':
                disabled = ''
                if AutoRefill.objects.filter(phone_number=phone_number.number, customer=phone_number.customer, trigger='SC') and\
                        phone_number.company.block_duplicate_schedule:
                    disabled = ' style=\"background-color: grey; border-color: grey;\" disabled'
                ajax_response['aaData'].append([type(phone_number).__name__, 'Phone number: <b>' + phone_number.number + '</b>'
                                                ' for <a href=\'%s' % (reverse('customer_update', args=[phone_number.customer.id])) +
                                                '\'>' + phone_number.customer.__unicode__() + '</a> '
                                                + ' <div style=\"float: right\">' + ' <a href=\'%s' %
                                                reverse('charge_list') + '?cid=' + str(phone_number.customer.id) +
                                                '\' style=\"background-color: green\" class=\'btn '
                                                'btn-primary btn-xs\'>Add cash payment</a> '
                                                + ' <a href=\'%s' % reverse('manualrefill') + '?ph=' + phone_number.number + '&cid=' +
                                                str(phone_number.customer.id) + '&lp=t' + '\' style=\"background-color: #366\"'
                                                                             ' class=\'btn '
                                                                             'btn-primary btn-xs\'>Recharge With Last'
                                                                             ' Plan</a> ' + '<a href=\'%s' %
                                                reverse('manualrefill') + '?ph=' + phone_number.number + '&cid=' + str(phone_number.customer.id) +
                                                '\' class=\'btn btn-primary btn-xs\'>Recharge Now</a>' +
                                                ' <a style=\"background-color: #4BA2B7;\" href=\'%s' %
                                                reverse('autorefill_create') + '?ph=' + phone_number.number +
                                                '&cid=' + str(phone_number.customer.id) + '&lp=t' +
                                                '\' class=\'btn btn-info btn-xs\'' + disabled + '>' +
                                                'Schedule With Last Plan</a>' +
                                                ' <a href=\'%s' % reverse('autorefill_create') + '?ph=' + phone_number.number +
                                                '&cid=' + str(phone_number.customer.id) + '\' class=\'btn btn-info btn-xs\''
                                                                             + disabled + '>'
                                                                             'Schedule Latter</a>' + '</div>'])
        # filtering company's customers
        customers = []
        filtered_customers = []
        customers_id = []
        if numbers:
            # first filtering customers by numbers
            for phone in PhoneNumber.objects.filter(reduce(operator.and_, (Q(number__icontains=number)
                                                                           for number in numbers)),
                                                    company=request.user.profile.company).exclude(customer=None):
                if not phone.customer.id in customers_id:
                    customers.append(phone.customer)
                    customers_id.append(phone.customer.id)
            # also filtering by card
            for customer_by_card in Customer.objects.filter(reduce(operator.or_, (Q(creditcard__endswith=val)
                                                                                  for val in numbers)),
                                                            company=request.user.profile.company):
                print customer_by_card
                add = True
                for customer in customers:
                    if customer.id == customer_by_card.id:
                        add = False
                        break
                if add:
                    customers.append(customer_by_card)
        # if there is no words
        if not names:
            filtered_customers = customers
        else:
            if customers:
                for customer in customers:
                    append = True
                    for name in names:
                        if not name.upper() in customer.__unicode__().upper():
                            append = False
                            break
                    if append:
                        filtered_customers.append(customer)
            else:
                filtered_customers = Customer.objects.filter(reduce(operator.and_, (Q(first_name__icontains=val) |
                                                                                    Q(middle_name__icontains=val) |
                                                                                    Q(last_name__icontains=val)
                                                                                    for val in filters)),
                                                             company=request.user.profile.company)
        for customer in filtered_customers:
            ajax_response['iTotalRecords'] += 1
            if request.GET['sSearch_0'] == '' or request.GET['sSearch_0'] == 'All'\
                    or request.GET['sSearch_0'] == 'Customers':
                unused_charges_count = ' Unused charges: <b>' + str(Charge.objects.filter(used=False, customer=customer).count()) + '</b>'
                unused_charges_amount = 0
                for charge in Charge.objects.filter(used=False, customer=customer):
                    unused_charges_amount += charge.amount
                if just_number:
                    disabled = ''
                    if AutoRefill.objects.filter(phone_number=number, customer=customer, trigger='SC') and\
                            customer.company.block_duplicate_schedule:
                        disabled = ' style=\"background-color: grey; border-color: grey;\" disabled'
                    ajax_response['aaData'].append([type(customer).__name__,
                                                    '<a href=\'%s' % (reverse('customer_update', args=[customer.id])) +
                                                    '\'>' + customer.__unicode__() + '</a>' +
                                                    ' (<b>' + customer.get_charge_getaway_display() + '</b>)'
                                                    + unused_charges_count + ' (<b>$' + str(unused_charges_amount)
                                                    + '</b>)' + ' <div style=\"float: right\">' + ' <a href=\'%s' %
                                                    reverse('charge_list') + '?cid=' + str(customer.id) +
                                                    '\' style=\"background-color: green\" class=\'btn '
                                                    'btn-primary btn-xs\'>Add cash payment</a> '
                                                    + ' <a href=\'%s' % reverse('manualrefill') + '?ph=' + number + '&cid=' +
                                                    str(customer.id) + '&lp=t' + '\' style=\"background-color: #366\"'
                                                                                 ' class=\'btn '
                                                                                 'btn-primary btn-xs\'>Recharge With Last'
                                                                                 ' Plan</a> ' + '<a href=\'%s' %
                                                    reverse('manualrefill') + '?ph=' + number + '&cid=' + str(customer.id) +
                                                    '\' class=\'btn btn-primary btn-xs\'>Recharge Now</a>' +
                                                    ' <a style=\"background-color: #4BA2B7;\" href=\'%s' %
                                                    reverse('autorefill_create') + '?ph=' + number +
                                                    '&cid=' + str(customer.id) + '&lp=t' +
                                                    '\' class=\'btn btn-info btn-xs\'' + disabled + '>' +
                                                    'Schedule With Last Plan</a>' +
                                                    ' <a href=\'%s' % reverse('autorefill_create') + '?ph=' + number +
                                                    '&cid=' + str(customer.id) + '\' class=\'btn btn-info btn-xs\''
                                                                                 + disabled + '>'
                                                                                 'Schedule Latter</a>' + '</div>'])
                else:
                    ajax_response['aaData'].append([type(customer).__name__,
                                                    '<a href=\'%s' % (reverse('customer_update', args=[customer.id])) +
                                                    '\'>' + customer.__unicode__() + '</a>' +
                                                    ' (<b>' + customer.get_charge_getaway_display() + '</b>)' +
                                                    unused_charges_count + ' (<b>$' + str(unused_charges_amount)
                                                    + '</b>)'])
        # filtering company's autorefills
        for autorefill in AutoRefill.objects.filter(reduce(operator.and_, (Q(customer__first_name__icontains=val) |
                                                                           Q(customer__middle_name__icontains=val) |
                                                                           Q(customer__last_name__icontains=val) |
                                                                           Q(phone_number__icontains=val)
                                                                           for val in filters)),
                                                    Q(trigger=AutoRefill.TRIGGER_AP) | Q(trigger=AutoRefill.TRIGGER_SC),
                                                    company=request.user.profile.company):
            ajax_response['iTotalRecords'] += 1
            if request.GET['sSearch_0'] == '' or request.GET['sSearch_0'] == 'All'\
                    or request.GET['sSearch_0'] == 'AutoRefills':
                last_renewal = ''
                if autorefill.last_renewal_date:
                    last_renewal = 'Last renewal: <b>' + str(autorefill.last_renewal_date) + '</b>'
                enabled = '</a><span class="fa fa-minus-circle text-danger"></span>'
                if autorefill.enabled:
                    enabled = '</a><span class="fa fa-check-circle text-success"></span>'
                ajax_response['aaData'].append([type(autorefill).__name__,
                                                '<a href=\'%s' % (reverse('autorefill_update', args=[autorefill.id])) +
                                                '\'>Scheduled Refill #' + autorefill.__unicode__() + '</a> ' + enabled +
                                                ' for <b>' + autorefill.phone_number + '</b> Plan: <b>'
                                                + autorefill.plan.__unicode__() + '</b> Scheduled for: <b>'
                                                + str(autorefill.renewal_date) + '</b> ' + last_renewal])
        # filtering company's transactions
        for transaction in Transaction.objects.filter(reduce(operator.and_, (Q(customer_str__icontains=val) |
                                                                             Q(phone_number_str__icontains=val) |
                                                                             Q(pin__icontains=val)
                                                                             for val in filters)),
                                                      company=request.user.profile.company).order_by('-started'):
            ajax_response['iTotalRecords'] += 1
            if request.GET['sSearch_0'] == '' or request.GET['sSearch_0'] == 'All'\
                    or request.GET['sSearch_0'] == 'Transactions':
                ended = ''
                if transaction.ended:
                    ended = 'Ended: <b>' +\
                            transaction.ended.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %I:%M:%S%p") + '</b>'
                ajax_response['aaData'].append([type(transaction).__name__,
                                                '<a href=\'%s' % (reverse('transaction_detail', args=[transaction.id])) +
                                                '\'>Transaction #' + transaction.__unicode__() + '</a> (<b>'
                                                + str(transaction.get_status_display()) + '</b>) for <b>'
                                                + transaction.phone_number_str + '</b> (<b>' +
                                                str(transaction.get_state_display()) + '</b>) Plan: <b>'
                                                + transaction.plan_str + '</b> ' + ended])
        # filtering company's charges
        for charge in Charge.objects.filter(reduce(operator.and_, (Q(customer__first_name__icontains=val) |
                                                                   Q(customer__middle_name__icontains=val) |
                                                                   Q(customer__last_name__icontains=val) |
                                                                   Q(creditcard__endswith=val) |
                                                                   Q(autorefill__phone_number__icontains=val) for val in filters)),
                                            company=request.user.profile.company).order_by('-created'):
            ajax_response['iTotalRecords'] += 1
            if request.GET['sSearch_0'] == '' or request.GET['sSearch_0'] == 'All'\
                    or request.GET['sSearch_0'] == 'Charges':
                cc_last_four = ''
                used = '</a><span class="fa fa-minus-circle text-danger"></span>'
                used_for = ' used for '
                if charge.creditcard:
                    cc_last_four = ' *' + charge.creditcard[-4:]
                if charge.used:
                    used = '</a><span class="fa fa-check-circle text-success"></span>'
                if charge.autorefill:
                    if charge.autorefill.phone_number:
                        used_for += '<b>' + charge.autorefill.phone_number + '</b>'
                ajax_response['aaData'].append([type(charge).__name__,
                                                '<a href=\'%s' % (reverse('charge_detail', args=[charge.id])) +
                                                '\'>Credit Card Charge #' + charge.__unicode__() + cc_last_four + '</a> '
                                                + used + ' (<b>'
                                                + charge.get_status_display() + '</b>)' + used_for + ' Amount: <b>'
                                                + str(charge.amount) + '</b> Created: <b>' +
                                                charge.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y "
                                                                                                           "%I:%M:%S%p") +
                                                '</b>'])
        for unused_pin in UnusedPin.objects.filter(reduce(operator.and_, (Q(pin__icontains=val) for val in filters)),
                                                   company=request.user.profile.company):
            ajax_response['iTotalRecords'] += 1
            if request.GET['sSearch_0'] == '' or request.GET['sSearch_0'] == 'All'\
                    or request.GET['sSearch_0'] == 'UnusedPins':
                used = '<span class="fa fa-minus-circle text-danger"></span>'
                transaction = ''
                if unused_pin.used:
                    used = '<span class="fa fa-check-circle text-success"></span>'
                if unused_pin.transaction:
                    transaction = ' Transaction: ' + '<a href=\'%s' % (reverse('transaction_detail',
                                                                              args=[unused_pin.transaction.id]))\
                                  + '\'>' + unused_pin.transaction.__unicode__() + '</a>'
                ajax_response['aaData'].append([type(unused_pin).__name__,
                                                '<a href=\'%s' % (reverse('unusedpin_update', args=[unused_pin.id])) +
                                                '\'>Unused Pin #' + unused_pin.__unicode__() +
                                                ' </a>' + used + transaction + ' Created: <b>'
                                                + unused_pin.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y "
                                                                                                                 "%I:%M:%S%p")
                                                + '</b> Updated: <b>'
                                                + unused_pin.updated.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y "
                                                                                                                 "%I:%M:%S%p")
                                                + '</b>'])
        ajax_response['iTotalDisplayRecords'] = len(ajax_response['aaData'])
        start = int(request.GET['iDisplayStart'])
        length = int(request.GET['iDisplayLength'])
        ajax_response['aaData'] = ajax_response['aaData'][start:start+length]
        json_data = json.dumps(ajax_response)
        return HttpResponse(json_data, content_type='application/json')


def ajax_refill_as_walk_in(request):
    if request.GET['number'].isdigit() and len(request.GET['number']) == 10 and\
            not PhoneNumber.objects.filter(number=request.GET['number'], company=request.user.profile.company):
        customer = Customer.objects.create(company=request.user.profile.company, user=request.user, first_name='Walk',
                                           last_name='in', charge_type=Customer.CASH, charge_getaway=Customer.CASH,
                                           primary_email='', zip='', usaepay_custid='', sms_email=request.GET['number'])
        PhoneNumber.objects.create(company=request.user.profile.company, customer=customer,
                                   number=request.GET['number'])
        return HttpResponse(json.dumps({'valid': True, 'id': str(customer.id)}), content_type='application/json')
    else:
        return HttpResponse(json.dumps({'valid': False, 'error': 'Customer with that number already exist.'}), content_type='application/json')


def customer_transactions(request, pk):
    return render(request, 'core/customer_transactions.html', {'customer_transactions': pk,
                                                               'full_name': Customer.objects.get(id=pk).__unicode__()})


def ajax_customer_transactions(request):
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [], 'iTotalRecords': 0}
    for transaction in Transaction.objects.filter(autorefill__customer__id=request.GET['customer_transactions']).order_by('-started'):
        ajax_response['iTotalRecords'] += 1
        ended = ''
        if transaction.ended:
            ended = 'Ended: <b>' +\
                    transaction.ended.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %I:%M:%S%p") + '</b>'
        ajax_response['aaData'].append([type(transaction).__name__,
                                        '<a href=\'%s' % (reverse('transaction_detail', args=[transaction.id])) +
                                        '\'>Transaction #' + transaction.__unicode__() + '</a> (<b>'
                                        + str(transaction.get_status_display()) + '</b>) for <b>'
                                        + transaction.phone_number_str + '</b> (<b>' +
                                        str(transaction.get_state_display()) + '</b>) ' + ended])
    ajax_response['iTotalDisplayRecords'] = len(ajax_response['aaData'])
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    ajax_response['aaData'] = ajax_response['aaData'][start:start+length]
    json_data = json.dumps(ajax_response)
    return HttpResponse(json_data, content_type='application/json')


def customer_autorefills(request, pk):
    return render(request, 'core/customer_autorefills.html', {'customer_autorefills': pk,
                                                              'full_name': Customer.objects.get(id=pk).__unicode__()})


def ajax_customer_autorefills(request):
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [], 'iTotalRecords': 0}
    for autorefill in AutoRefill.objects.filter(customer__id=request.GET['customer_autorefills']):
        ajax_response['iTotalRecords'] += 1
        last_renewal = ''
        if autorefill.last_renewal_date:
            last_renewal = 'Last renewal: <b>' + str(autorefill.last_renewal_date) + '</b>'
        enabled = '</a><span class="fa fa-minus-circle text-danger"></span>'
        if autorefill.enabled:
            enabled = '</a><span class="fa fa-check-circle text-success"></span>'
        ajax_response['aaData'].append([type(autorefill).__name__,
                                        '<a href=\'%s' % (reverse('autorefill_update', args=[autorefill.id])) +
                                        '\'>Scheduled Refill #' + autorefill.__unicode__() + '</a> ' + enabled +
                                        ' for <b>' + autorefill.phone_number + '</b> Scheduled for: <b>'
                                        + str(autorefill.renewal_date) + '</b> ' + last_renewal])
    ajax_response['iTotalDisplayRecords'] = len(ajax_response['aaData'])
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    ajax_response['aaData'] = ajax_response['aaData'][start:start+length]
    json_data = json.dumps(ajax_response)
    return HttpResponse(json_data, content_type='application/json')


def customer_cc_charges(request, pk):
    return render(request, 'core/customer_cc_charges.html', {'customer_cc_charges': pk,
                                                             'full_name': Customer.objects.get(id=pk).__unicode__()})


def ajax_customer_cc_charges(request):
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [], 'iTotalRecords': 0}
    for charge in Charge.objects.filter(customer__id=request.GET['customer_cc_charges']).order_by('-created'):
        ajax_response['iTotalRecords'] += 1
        cc_last_four = ''
        used = '</a><span class="fa fa-minus-circle text-danger"></span>'
        used_for = ' used for '
        transaction = ''
        if charge.creditcard:
            cc_last_four = ' *' + charge.creditcard[-4:]
        if charge.used:
            used = '</a><span class="fa fa-check-circle text-success"></span>'
        if charge.autorefill:
            if charge.autorefill.phone_number:
                used_for += '<b>' + charge.autorefill.phone_number + '</b>'
        if TransactionCharge.objects.filter(charge=charge):
            transaction = ' Transaction : <b>' + str(TransactionCharge.objects.filter(charge=charge)[0].transaction.id)\
                          + '</b>'
        ajax_response['aaData'].append([type(charge).__name__,
                                        '<a href=\'%s' % (reverse('charge_detail', args=[charge.id])) +
                                        '\'>Credit Card Charge #' + charge.__unicode__() + cc_last_four + '</a> '
                                        + used + ' (<b>'
                                        + charge.get_status_display() + '</b>)' + used_for + ' Created: <b>' +
                                        charge.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y "
                                                                                                   "%I:%M:%S%p") +
                                        '</b>' + transaction])
    ajax_response['iTotalDisplayRecords'] = len(ajax_response['aaData'])
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    ajax_response['aaData'] = ajax_response['aaData'][start:start+length]
    json_data = json.dumps(ajax_response)
    return HttpResponse(json_data, content_type='application/json')


def ajax_schedule_monthly(request, pk):
    manual_refill = AutoRefill.objects.get(id=int(pk))
    if manual_refill.customer.company == request.user.profile.company:
        if not (AutoRefill.objects.filter(customer=manual_refill.customer, trigger='SC',
                                          phone_number=manual_refill.phone_number) and
                request.user.profile.company.block_duplicate_schedule):
            delta = datetime.timedelta()
            if manual_refill.plan.carrier.renew_days:
                delta = datetime.timedelta(days=manual_refill.plan.carrier.renew_days)
            elif manual_refill.plan.carrier.renew_months:
                delta = relativedelta(months=manual_refill.plan.carrier.renew_months)
            next_renewal_date = manual_refill.renewal_date
            while next_renewal_date <= datetime.datetime.now(pytz.timezone('US/Eastern')).date():
                next_renewal_date += delta
            scheduler_refill = AutoRefill.objects.create(user=request.user, company=request.user.profile.company,
                                                         customer=manual_refill.customer, plan=manual_refill.plan,
                                                         phone_number=manual_refill.phone_number, trigger='SC',
                                                         renewal_date=next_renewal_date)
        else:
            return HttpResponse(json.dumps({'valid': False, 'error': 'Scheduler refill for this customer and this'
                                                                     ' number already exists.'}),
                                content_type='application/json')
        return HttpResponse(json.dumps({'valid': True, 'id': str(scheduler_refill.id)}), content_type='application/'
                                                                                                      'json')
    else:
        return HttpResponse(json.dumps({'valid': False, 'error': 'This user is not from your company.'}),
                            content_type='application/json')


def ajax_last_transaction_data(request):
    if Transaction.objects.filter(phone_number_str=request.GET['phone_number']):
        return HttpResponse(json.dumps({'exist': True,
                                        'carrier': Transaction.objects.filter(phone_number_str=request.GET['phone_number']).order_by('-id')[0].autorefill.plan.carrier.id,
                                        'plan': Transaction.objects.filter(phone_number_str=request.GET['phone_number']).order_by('-id')[0].autorefill.plan.id,
                                        'refill_type': Transaction.objects.filter(phone_number_str=request.GET['phone_number']).order_by('-id')[0].autorefill.refill_type}),
                            content_type='application/json')
    else:
        return HttpResponse(json.dumps({'exist': False,
                                        'carrier': '',
                                        'plan': '',
                                        'refill_type': ''}),
                            content_type='application/json')


def ajax_skip_next_refill(request):
    if request.GET['id'] and AutoRefill.objects.filter(id=request.GET['id']):
        autorefill = AutoRefill.objects.filter(id=request.GET['id'])[0]
        autorefill.set_renewal_date_to_next(today=autorefill.renewal_date)
        autorefill.save()
        renewal_date = "%s/%s/%s" % (autorefill.renewal_date.month, autorefill.renewal_date.day,
                                     autorefill.renewal_date.year)
        end_renewal_date = ''
        if autorefill.renewal_end_date:
            end_renewal_date = "%s/%s/%s" % (autorefill.renewal_end_date.month, autorefill.renewal_end_date.day,
                                             autorefill.renewal_end_date.year)
        return HttpResponse(json.dumps({
            'valid': True,
            'renewal_date': renewal_date,
            'end_renewal_date': end_renewal_date,
        }))
    else:
        return HttpResponse(json.dumps({
            'valid': False,
        }))



def compare_pins_with_dollarphone(request):
    company = request.user.profile.company
    if not company.dollar_user or not company.dollar_pass:
        message = "Dollarphone account is missing in company. Please correct one of these to proceed"
        messages.add_message(request, messages.ERROR, '%s' % message)
        return HttpResponseRedirect(reverse('pinreport_list'))
    queue_compare_pins_with_dollarphone.delay(company.id)
    messages.add_message(request, messages.SUCCESS, 'Compare started')
    return HttpResponseRedirect(reverse('pinreport_list'))


def pinreport_download(request, order_id):
    pinreport = PinReport.objects.get(id=order_id)
    template = request.GET.get('template')
    response = HttpResponse(mimetype='text/csv')
    filename = 'Pin_report.csv'
    writer = csv.writer(response)
    writer.writerow(['Pins'])
    if template != 'true':
        filename = 'scheduledrefills_export_{d.month}_{d.day}_{d.year}.csv'.format(d=datetime.datetime.now())
        for pin in pinreport.report.split('<br/>'):
            writer.writerow([pin])
    response['Content-Disposition'] = 'attachment;filename=%s' % filename
    return response


def ajax_log(request):
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [],
                     'iTotalRecords': Log.objects.filter(company=request.user.profile.company).count(),
                     'iTotalDisplayRecords': Log.objects.filter(company=request.user.profile.company,
                                                                note__icontains=request.GET['sSearch']).count()}
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    if request.GET['sSortDir_0'] == 'asc':
        count_up = start+1
        for log in Log.objects.filter(company=request.user.profile.company,
                                      note__icontains=request.GET['sSearch']).order_by('created')[start:start+length]:
            log_details = ''
            if len(log.note.split('\n')) > 1:
                log_details = log.note.split('\n')[1]
            ajax_response["aaData"].append([count_up,
                                        log.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %I:%M:%S%p"),
                                        '<div><div style=\"cursor: pointer; padding: 0px;\"'
                                        ' class=\"panel-heading accordion-toggle collapsed\"'
                                        ' data-toggle=\"collapse\" data-target=\"#collapse'+str(log.id)+'\">'
                                        +log.note.split('\n')[0]+'</div></div>'+'<div id=\"collapse'+str(log.id)+
                                        '\" class=\"panel-collapse collapse\">'+log_details+'</div>'])
            count_up += 1
    else:
        count_up = int(ajax_response['iTotalDisplayRecords'])-start
        for log in Log.objects.filter(company=request.user.profile.company,
                                      note__icontains=request.GET['sSearch']).order_by('-created')[start:start+length]:
            log_details = ''
            if len(log.note.split('\n')) > 1:
                log_details = log.note.split('\n')[1]
            ajax_response["aaData"].append([count_up,
                                        log.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %I:%M:%S%p"),
                                        '<div><div style=\"cursor: pointer; padding: 0px;\"'
                                        ' class=\"panel-heading accordion-toggle collapsed\"'
                                        ' data-toggle=\"collapse\" data-target=\"#collapse'+str(log.id)+'\">'
                                        +log.note.split('\n')[0]+'</div></div>'+'<div id=\"collapse'+str(log.id)+
                                        '\" class=\"panel-collapse collapse\">'+log_details+'</div>'])
            count_up -= 1
    json_data = json.dumps(ajax_response)
    return HttpResponse(json_data, content_type='application/json')


def ajax_carriers_list(request):
    orders = [('0', 'name'), ('1', 'recharge_number'), ('2', 'renew_days'),
              ('3', 'renew_months'), ('4', 'created'), ('5', 'updated')]
    order_by = 'name'
    for order in orders:
        if order[0] == request.GET['iSortCol_0']:
            order_by = order[1]
            break
    direction = ''
    if request.GET['sSortDir_0'] == 'desc':
        direction = '-'
    filters = request.GET['sSearch'].split(' ')
    filtered = Carrier.objects.filter(reduce(operator.and_, (Q(name__icontains=val) |
                                                             Q(recharge_number__icontains=val) |
                                                             Q(renew_days__icontains=val) |
                                                             Q(renew_months__icontains=val)
                                                             for val in filters)))
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [],
                     'iTotalRecords': Carrier.objects.filter(company=request.user.profile.company).count(),
                     'iTotalDisplayRecords': filtered.count()}
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    for carrier in filtered.order_by(direction+order_by)[start:start+length]:
        name = carrier.name
        if request.user.is_superuser:
            name = '<a href=\'%s' % (reverse('carrier_update', args=[carrier.id])) + '\'>' + carrier.name + '</a>'
        ajax_response['aaData'].append(['<img src=\"/static/img/' + slugify(carrier) + '.jpg\"'
                                        ' style=\"width:32px;\" >' + name, carrier.recharge_number, carrier.renew_days,
                                        carrier.renew_months,
                                        carrier.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M"),
                                        carrier.updated.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M")])
    return HttpResponse(json.dumps(ajax_response), content_type='application/json')


def ajax_customers_list(request):
    orders = [('0', 'first_name'), ('1', 'last_name'), ('7', 'charge_type'),
              ('8', 'charge_getaway'), ('9', 'enabled'), ('10', 'created'), ('11', 'updated')]
    order_by = 'first_name'
    for order in orders:
        if order[0] == request.GET['iSortCol_0']:
            order_by = order[1]
            break
    direction = ''
    if request.GET['sSortDir_0'] == 'desc':
        direction = '-'
    filters = request.GET['sSearch'].split(' ')
    filtered = Customer.objects.filter(reduce(operator.and_, (Q(first_name__icontains=val) |
                                                              Q(last_name__icontains=val) |
                                                              Q(primary_email__icontains=val) |
                                                              Q(city__icontains=val) |
                                                              Q(state__icontains=val) |
                                                              Q(zip__icontains=val)
                                                              for val in filters)),
                                       company=request.user.profile.company)
    for charge_getaway_choice in Customer.CHARGE_GETAWAY_CHOICES:
        if charge_getaway_choice[1] == request.GET['sSearch_8']:
            filtered = filtered.filter(charge_getaway=charge_getaway_choice[0])
            break
    if request.GET['sSearch_9'] == 'Enabled':
        filtered = filtered.filter(enabled=True)
    elif request.GET['sSearch_9'] == 'Disabled':
        filtered = filtered.filter(enabled=False)
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [],
                     'iTotalRecords': Customer.objects.filter(company=request.user.profile.company).count(),
                     'iTotalDisplayRecords': filtered.count()}
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    for customer in filtered.order_by(direction+order_by)[start:start+length]:
        payment_type = ''
        for charge_type_choice in Customer.CHARGE_TYPE_CHOICES:
            if charge_type_choice[0] == customer.charge_type:
                payment_type = charge_type_choice[1]
                break
        payment_gateway = ''
        for charge_getaway_choice in Customer.CHARGE_GETAWAY_CHOICES:
            if charge_getaway_choice[0] == customer.charge_getaway:
                payment_gateway = charge_getaway_choice[1]
                break
        ajax_response['aaData'].append(['<a href=\'%s' % (reverse('customer_update', args=[customer.id])) + '\'>' +
                                        customer.first_name + '</a>', customer.last_name, customer.primary_email,
                                        customer.phone_numbers_list(), customer.city, customer.state, customer.zip,
                                        payment_type, payment_gateway, customer.enabled,
                                        customer.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M"),
                                        customer.updated.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M")
                                        ])
    return HttpResponse(json.dumps(ajax_response), content_type='application/json')


def ajax_transactions_list(request):
    orders = [('1', 'customer_str'), ('3', 'plan_str'), ('4', 'refill_type_str'),
              ('6', 'state'), ('7', 'status'), ('9', 'completed'), ('11', 'started'), ('12', 'ended')]
    order_by = 'started'
    for order in orders:
        if order[0] == request.GET['iSortCol_0']:
            order_by = order[1]
            break
    direction = ''
    if request.GET['sSortDir_0'] == 'desc':
        direction = '-'
    filters = request.GET['sSearch'].split(' ')
    filtered = Transaction.objects.filter(reduce(operator.and_, (Q(id__icontains=val) |
                                                                 Q(customer_str__icontains=val) |
                                                                 Q(plan_str__icontains=val) |
                                                                 Q(refill_type_str__icontains=val) |
                                                                 Q(pin__icontains=val) |
                                                                 Q(phone_number_str__icontains=val)
                                                                 for val in filters)),
                                          status__icontains=request.GET['sSearch_7'],
                                          state__icontains=request.GET['sSearch_6'])
    if request.GET['sSearch_5'] == 'WP':
        filtered = filtered.exclude(pin__isnull=True).exclude(pin='')
    if request.GET['sSearch_5'] == 'WO':
        filtered = filtered.filter(Q(pin='') | Q(pin=None))
    if not request.user.is_superuser:
        filtered = filtered.filter(company=request.user.profile.company)
    if request.GET['sSearch_8'] == 'True':
        filtered = filtered.filter(paid=True)
    elif request.GET['sSearch_8'] == 'False':
        filtered = filtered.filter(paid=False)
    if request.GET['sSearch_9'] == 'True':
        filtered = filtered.filter(completed=True)
    elif request.GET['sSearch_9'] == 'False':
        filtered = filtered.filter(completed=False)
    ajax_response = {"sEcho": request.GET['sEcho'], "aaData": [],
                     'iTotalRecords': Transaction.objects.filter(company=request.user.profile.company).count(),
                     'iTotalDisplayRecords': filtered.count()}
    start = int(request.GET['iDisplayStart'])
    length = int(request.GET['iDisplayLength'])
    for transaction in filtered.order_by(direction+order_by)[start:start+length]:
        customer = transaction.customer_str
        cc_charge = ''
        if transaction.autorefill:
            customer = '<a href=\'%s' % (reverse('customer_update', args=[transaction.autorefill.customer.id])) + '\'>' +\
                       transaction.customer_str + '</a>'
        if TransactionCharge.objects.filter(transaction=transaction):
            cc_charge = '<a href=\'%s' % (reverse('charge_detail',
                                                  args=[TransactionCharge.objects.filter(transaction=transaction)[0].charge.id])) + '\'>' +\
                        str(TransactionCharge.objects.filter(transaction=transaction)[0].charge.id) + '</a>'
        ajax_response['aaData'].append(['<a href=\'%s' % (reverse('transaction_detail', args=[transaction.id])) + '\'>' +
                                        str(transaction.id) + '</a>', customer, transaction.phone_number_str,
                                        transaction.plan_str, transaction.refill_type_str, transaction.pin,
                                        transaction.state, transaction.status, transaction.paid, transaction.completed,
                                        cc_charge,
                                        transaction.started.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M"),
                                        transaction.ended.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M")
                                        ])
    return HttpResponse(json.dumps(ajax_response), content_type='application/json')


def ajax_transaction(request, pk):
    transaction = Transaction.objects.get(id=pk)
    steps = TransactionStep.objects.filter(transaction=pk).order_by('created')
    step_list = []
    pin = transaction.get_pin_url()
    for step in steps:
        if 'receipt' in step.adv_status:
            pin = '%sa>' % step.adv_status[step.adv_status.find('<a'):step.adv_status.rfind('a>')].replace('>receipt<', '>%s<' % transaction.pin)
        step_obj = {
                'operation': step.operation,
                'action': step.action,
                'status': step.status,
                'status_str': step.get_status_display(),
                'adv_status': step.adv_status,
                'created': step.created.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %I:%M:%S%p"),
        }
        step_list.append(step_obj)
    trigger = transaction.trigger
    if transaction.autorefill:
        trigger = transaction.autorefill.trigger
    data = {
        'steps': step_list,
        'transaction': {
            'triggered_by': transaction.triggered_by,
            'user': transaction.user.id,
            'company': transaction.company.id,
            'customer': transaction.customer_str,
            'phone_number': transaction.phone_number_str,
            'plan': transaction.plan_str,
            'refill_type': transaction.refill_type_str,
            'pin': pin,
            'state': transaction.state,
            'state_str': transaction.get_state_display(),
            'status': transaction.status,
            'status_str': transaction.get_status_display(),
            'paid': transaction.paid,
            'completed': transaction.completed,
            'adv_status': transaction.adv_status,
            'current_step': transaction.current_step,
            'autorefill_trigger': trigger,
            'profit': str(transaction.profit),
            'started': transaction.started.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M:%S"),
            'ended': transaction.ended.astimezone(timezone('US/Eastern')).strftime("%m/%d/%y %H:%M:%S"),
        },
    }
    return render_to_json_response(data)


def ajax_mark_transaction(request, pk):
    button = request.GET.get('button')
    transaction = Transaction.objects.get(id=int(pk))
    operation = ''
    adv_status = ''
    if button == 'paid':
        operation = 'Mark paid'
        adv_status = 'User %s marked transaction as paid.' % request.user
    elif button == 'closed':
        operation = 'Closed'
        adv_status = 'User %s closed transaction.' % request.user
    elif button == 'restarted':
        operation = 'Restarted'
        adv_status = 'User %s restarted transaction.' % request.user
    elif button == 'completed':
        operation = 'Mark completed'
        adv_status = 'User %s marked transaction as completed.' % request.user
    elif button == 'completed-with-pin':
        pin = request.GET.get('pin')
        transaction.pin = pin
        operation = 'Mark completed with pin'
        adv_status = 'User %s marked transaction as completed with pin %s.' % (request.user, pin)
    elif button == 'prerefill_restart':
        operation = 'Restarted charge and get pin'
        adv_status = 'User %s restarted transaction for charge and get pin steps.' % request.user
    transaction.add_transaction_step(operation,
                                     'button',
                                     TransactionStep.SUCCESS,
                                     adv_status)
    if button == 'prerefill_restart':
        transaction.retry_count = 0
        transaction.state = Transaction.RETRY
        transaction.adv_status = 'prerefill restarted by user %s' % request.user
        transaction.save()
        queue_prerefill.delay(transaction.id)
        return HttpResponse()
    # restart transaction
    if button == 'restarted':
        transaction.retry_count = 0
        transaction.state = Transaction.RETRY
        transaction.adv_status = 'Transaction restarted by user %s' % request.user
        transaction.save()
        queue_refill.delay(transaction.id)
        return HttpResponse()
    # pay transaction
    if button == 'paid':
        transaction.paid = True
        transaction.save()
        if transaction.company.use_sellercloud and transaction.sellercloud_order_id:
            try:
                transaction.send_payment_to_sellercloud_order()
            except Exception, e:
                transaction.add_transaction_step('notification',
                                                 'SellerCloud',
                                                 TransactionStep.ERROR,
                                                 u'%s' % e)
        return HttpResponse()
    # close transaction
    transaction.state = Transaction.COMPLETED
    transaction.save()
    if button == 'closed':
        return HttpResponse()
    # complete transaction
    transaction.completed = True
    transaction.status = Transaction.SUCCESS
    transaction.save()
    if transaction.company.use_asana:
        try:
            transaction.send_asana()
        except Exception, e:
            transaction.add_transaction_step('notification',
                                             'Asana',
                                             TransactionStep.ERROR,
                                             u'%s' % e)
    if transaction.company.use_sellercloud:
        try:
            transaction.send_tratsaction_to_sellercloud()
            transaction.send_note_to_sellercloud_order()
            transaction.send_payment_to_sellercloud_order()
        except Exception, e:
            transaction.add_transaction_step('notification',
                                             'SellerCloud',
                                             TransactionStep.ERROR,
                                             u'%s' % e)
    return HttpResponse()


def ajax_apply_send_pin_prerefill(request):
    CompanyProfile.objects.get(id=request.user.profile.company_id)\
        .set_customers_send_pin_prerefill(state=request.GET.get('send_pin_prerefill'))

    return HttpResponse()

def ajax_set_default_notification(request):
    CompanyProfile.objects.get(id=request.user.profile.company_id).set_default_notification()
    return HttpResponse()

def ajax_add_phone_number(request):
    customer_id = request.GET.get('customer')
    number = request.GET.get('number')
    customer = Customer.objects.get(id=customer_id)
    new_phone = PhoneNumber(company=customer.company, number=number, customer=customer)
    data = 'Number added to customer'
    try:
        new_phone.save()
    except Exception, e:
        logger.error('Add customer from REFILL form %s' % e)
        data = 'Number not added to customer'
    finally:
        return render_to_json_response(data)


def ajax_phone_numbers(request):
    id = request.GET.get('id')
    data = []
    if id:
        customer = Customer.objects.get(id=id)
        for phone in PhoneNumber.objects.filter(customer=customer):
            num = phone.number
            data.append({'text': num, 'value': num})
    return render_to_json_response(data)


def ajax_carrier_plans(request):
    id = request.GET.get('id')
    data = []
    if id:
        if cache.get(key=id):
            return render_to_json_response(cache.get(key=id))
        for plan in Plan.objects.filter(carrier=id).order_by('plan_id'):
                available = 'Not available'
                if plan.available:
                    available = 'available'
                obj = {
                    'pk': plan.id,
                    'id': plan.plan_id,
                    'name': plan.plan_name,
                    'cost': float(plan.plan_cost),
                    'type': plan.get_plan_type_display(),
                    'available': available,
                }
                data.append(obj)
    return render_to_json_response(data)


def ajax_carriers(request):
    if cache.get(key='carriers'):
        return render_to_json_response(cache.get(key='carriers'))
    carriers = Carrier.objects.all().order_by('name')
    carrier_list = []
    for carrier in carriers:
        obj = {
            'pk': carrier.id,
            'name': carrier.name,
            'name_slug': slugify(carrier.name),
            'admin_site': carrier.admin_site
        }
        carrier_list.append(obj)
    cache.set(key='carriers', value=carrier_list, timeout=6000)
    return render_to_json_response(carrier_list)


def ajax_carrier(request):
    carid = int(request.GET.get('carid'))
    carrier = Carrier.objects.get(id=carid)
    rs = {
        'name': carrier.name,
        'name_slug': slugify(carrier.name),
        'admin_site': carrier.admin_site,
        'default_time': carrier.default_time
    }
    return render_to_json_response(rs)


def ajax_transaction_summary(request):
    transaction_list = []
    today = django_tz.now()
    for day in range(1, today.day + 1):
        this_date = django_tz.make_aware((datetime.datetime.combine(today, datetime.time.min) - datetime.timedelta(days=(today.day-day))), timezone=timezone('US/Eastern'))
        next_date = django_tz.make_aware((datetime.datetime.combine(today, datetime.time.max) - datetime.timedelta(days=(today.day-day))), timezone=timezone('US/Eastern'))
        if request.user.is_superuser:
            obj = {
                'date': '{d.month}-{d.day}-{d.year}'.format(d=this_date),
                'Success': Transaction.objects.filter(started__range=[this_date, next_date], status='S').count(),
                'Failed': Transaction.objects.filter(started__range=[this_date, next_date], status='E').count(),
            }
        else:
            obj = {
                'date': '{d.month}-{d.day}-{d.year}'.format(d=this_date),
                'Success': Transaction.objects.filter(started__range=[this_date, next_date], status='S', company=request.user.profile.company).count(),
                'Failed': Transaction.objects.filter(started__range=[this_date, next_date], status='E', company=request.user.profile.company).count(),
            }
        transaction_list.append(obj)
    return render_to_json_response(transaction_list)


def ajax_pin_usage(request):
    today = datetime.date.today()
    month_days = calendar.monthrange(today.year,today.month)[1]
    start_month = datetime.date.today() - datetime.timedelta(days=(today.day-1))
    end_month = datetime.date.today() + datetime.timedelta(days=(month_days-today.day))
    if request.user.is_superuser:
        refills_done = AutoRefill.objects.filter(enabled=True, last_renewal_date__range=[start_month, today]).count()
        refills_pending = AutoRefill.objects.filter(enabled=True, renewal_date__range=[today, end_month]).count()
    else:
        refills_done = AutoRefill.objects.filter(company=request.user.profile.company, enabled=True, last_renewal_date__range=[start_month, today]).count()
        refills_pending = AutoRefill.objects.filter(company=request.user.profile.company, enabled=True, renewal_date__range=[today, end_month]).count()
    pin_usage = [
        {'label': "Used", 'value': refills_done},
        {'label': "Still Needed", 'value': refills_pending},
    ]
    if refills_pending == 0 and refills_done == 0:
        pin_usage = [
            {'label': 'None', 'value': 1}
        ]
    return render_to_json_response(pin_usage)


def ajax_customers(request):
    customers = Customer.objects.filter(company=request.user.profile.company, enabled=True).count()
    if request.user.is_superuser:
        customers = CompanyProfile.objects.filter(superuser_profile=False).count()
    return render_to_json_response({'result': customers})


def ajax_total_transactions(request):
    tot_trans = Transaction.objects.filter(company=request.user.profile.company, state='C').count()
    if request.user.is_superuser:
        tot_trans = Transaction.objects.filter(state='C').count()
    return render_to_json_response({'result': tot_trans})


def ajax_transaction_successrate(request):
    tot_trans = Transaction.objects.filter(company=request.user.profile.company, state=Transaction.COMPLETED).count()
    suc_trans = Transaction.objects.filter(company=request.user.profile.company, state=Transaction.COMPLETED, status=Transaction.SUCCESS).count()
    if request.user.is_superuser:
        tot_trans = Transaction.objects.filter(state=Transaction.COMPLETED).count()
        suc_trans = Transaction.objects.filter(state=Transaction.COMPLETED, status=Transaction.SUCCESS).count()
    if tot_trans > 0:
        success_rate = (float(suc_trans)/tot_trans)*100
    else:
        success_rate = 100
    return render_to_json_response({'result': int(success_rate)})


def ajax_transaction_profits(request):
    transactions = Transaction.objects.filter(company=request.user.profile.company, state='C', status='S')
    profits = 0
    for transaction in transactions:
        if transaction.profit:
            profits = transaction.profit + profits
    return render_to_json_response({'result': "{0:.2f}".format(profits)})


def ajax_need_pins_report(request):
    report = {}
    today = datetime.datetime.now(timezone('US/Eastern')).date()
    pin_for_day = {}
    pin_day_count = 0
    unused_pins = list(UnusedPin.objects.filter(company=request.user.profile.company, used=False))
    for i in range(0, 14):
        day_report = {}
        for autorefill in AutoRefill.objects.filter(company=request.user.profile.company,
                                                    enabled=True,
                                                    renewal_date=today + datetime.timedelta(days=i)):
            if autorefill_has_pin(autorefill):
                continue
            pin = has_unused_pin(unused_pins, autorefill.plan)
            if pin:
                unused_pins.remove(pin)
                continue
            if autorefill.plan.plan_id in pin_for_day:
                pin_for_day[autorefill.plan.plan_id] += 1
            else:
                pin_for_day[autorefill.plan.plan_id] = 1
            pin_day_count += 1
        day_report['pin_count'] = pin_day_count
        day = []
        for key in pin_for_day.keys():
            day.append('<dt>%s:</dt><dd>%s</dd>' % (key, pin_for_day[key]))
        day_text = '<br/>'.join(day)
        day_report['pins'] = '<dl class="dl-horizontal">%s</dl>' % day_text
        report[i] = day_report
    return render_to_json_response({'result': report})


def has_unused_pin(unused_pins, plan):
    for unused_pin in unused_pins:
        if unused_pin.plan == plan:
            return unused_pin
    return False


def autorefill_has_pin(autorefill):
    today = datetime.datetime.now(timezone('US/Eastern')).date()
    start_transaction_date = datetime.datetime.combine(today - datetime.timedelta(days=1), datetime.time(hour=11, minute=59))
    start_charge_date = datetime.datetime.combine(today - datetime.timedelta(days=autorefill.company.authorize_precharge_days), datetime.time(hour=04, minute=00))
    for transaction in Transaction.objects.filter(autorefill=autorefill, started__gt=start_transaction_date):
        if transaction.pin:
            return True
    for charge in Charge.objects.filter(autorefill=autorefill, created__gt=start_charge_date):
        if charge.pin:
            return True
    return False


def twilio_request(request):
    response = etree.Element('Response')
    print response
    record = etree.Element('Record', timeout="20", maxLength="20", finishOnKey="#", action=request.build_absolute_uri(reverse('twilio_response')), method="GET")
    print record
    response.append(record)
    print response
    return HttpResponse(etree.tostring(response, encoding="utf-8", xml_declaration=True), content_type='application/xml')


def twilio_response(request):
    print 'Response'
    cache.set(key=request.GET['CallSid'], value=request.GET['RecordingUrl'], timeout=600)
    print cache.get(key=request.GET['CallSid'])
    response = etree.Element('Response')
    print response
    return HttpResponse(etree.tostring(response, encoding="utf-8", xml_declaration=True), mimetype='application/xml')


def pparsb_response(request, pk):
    cache.set(key=pk, value=request.GET.dict(), timeout=600)
    logger.info('%s' % request.GET.dict())
    return HttpResponse("Done")


def render_to_json_response(context, **response_kwargs):
    data = json.dumps(context)
    response_kwargs['content_type'] = 'application/json'
    return HttpResponse(data, **response_kwargs)


class ImportLogView(ListView):
    model = ImportLog

    def get_queryset(self):
        return ImportLog.objects.order_by('-created').filter(company=self.request.user.profile.company)


def import_customers_from_usaepay(request):
    company = request.user.profile.company
    if not company.usaepay_username or not company.usaepay_password:
        message = 'no USAePay username/password for API requests'
        messages.add_message(request, messages.ERROR, '%s' % message)
        return HttpResponseRedirect(reverse('customer_list'))
    if not company.usaepay_source_key or not company.usaepay_pin:
        message = 'no USAePay tokens for API requests'
        messages.add_message(request, messages.ERROR, '%s' % message)
        return HttpResponseRedirect(reverse('customer_list'))
    queue_import_customers_from_usaepay.delay(company.id, request.user.id)
    messages.add_message(request, messages.SUCCESS, 'Your import starting now')
    return HttpResponseRedirect(reverse('customer_list'))


class PhoneNumbersImport(View):
    template_name = 'core/phone_number_import.html'
    form_class = forms.PhoneNumberImportForm

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {'form': form})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST, request.FILES)
        if form.is_valid():
            workbook = xlrd.open_workbook(file_contents=form.cleaned_data['file'].read())
            worksheet = workbook.sheet_by_index(0)
            # Change this depending on how many header rows are present
            # Set to 0 if you want to include the header data.
            offset = 0

            rows = []
            for i, row in enumerate(range(worksheet.nrows)):
                if i <= offset:  # (Optionally) skip headers
                    continue
                r = []
                for j, col in enumerate(range(worksheet.ncols)):
                    r.append(worksheet.cell_value(i, j))
                rows.append(r)
            queue_import_phone_numbers.delay(request.user.profile.company, rows)
            messages.add_message(self.request, messages.SUCCESS,
                                 'Import phone numbers job has been added to queue')
            return HttpResponseRedirect(reverse('customer_list'))
        else:
            return render(request, self.template_name, {'form': form, 'confirm': False})


def close_updates(request):
    company = request.user.profile.company
    company.show_updates = False
    company.save()
    return HttpResponse()


def change_user(request):
    user_profile = request.user.profile
    if request.GET['news_email'] == '':
        user_profile.updates_email = request.GET['news_email']
        user_profile.save()
        return HttpResponse(json.dumps({'valid': True}), content_type='application/json')
    try:
        emails = [email.strip(' ') for email in request.GET['news_email'].split(',') if email != '']
        email_on_save = ''
        for email in emails:
            validate_email(email)
            email_on_save += email + ','
        user_profile.updates_email = email_on_save
        user_profile.save()
        return HttpResponse(json.dumps({'valid': True}), content_type='application/json')
    except ValidationError:
        return HttpResponse(json.dumps({'valid': False}), content_type='application/json')
