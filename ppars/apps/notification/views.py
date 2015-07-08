from django.contrib import messages
from django.shortcuts import get_object_or_404, render
from django.views.generic import View
from django.views.generic.edit import CreateView

from models import SpamMessage, CustomPreChargeMessage
from forms import SpamMessageForm, CustomPreChargeMessageForm
from ppars.apps.notification.tasks import queue_send_sms


class SpamMessageCreate(CreateView):
    model = SpamMessage
    form_class = SpamMessageForm

    def form_valid(self, form):
        self.object = spam_message = form.save(commit=False)
        spam_message.company = self.request.user.profile.company
        if not self.request.user.profile.company.twilio_sid or \
                not self.request.user.profile.company.twilio_auth_token or \
                not self.request.user.profile.company.twilio_number:
            messages.add_message(self.request, messages.ERROR,
                                 'Twilio account is missing in company')
        else:

            spam_message.save()
            queue_send_sms.delay(spam_message.id)
            messages.add_message(self.request, messages.SUCCESS,
                                 'Messages will be send.')
        return super(SpamMessageCreate, self).form_valid(form)


class CustomPreChargeMessageDetail(View):
    model = CustomPreChargeMessage
    form_class = CustomPreChargeMessageForm
    template_name = 'notification/customprechargemessage_form.html'
    context_object_name = 'custom_precharge_message'

    def get(self, request, *args, **kwargs):
        if CustomPreChargeMessage.objects.filter(company=request.user.profile.company).exists():
            m = get_object_or_404(CustomPreChargeMessage, company=request.user.profile.company)
            form = CustomPreChargeMessageForm(instance=m)
            return render(request, self.template_name,
                          {
                              'form': form,
                              'custom_precharge_message': m,
                          })
        else:
            form = CustomPreChargeMessageForm()
            return render(request, self.template_name, {'form': form})

    def post(self, request, *args, **kwargs):
        if CustomPreChargeMessage.objects.filter(company=request.user.profile.company).exists():
            m = get_object_or_404(CustomPreChargeMessage, company=request.user.profile.company)
            form = CustomPreChargeMessageForm(request.POST, instance=m)
            if form.is_valid():
                self.object = m = form.save()
                messages.add_message(self.request, messages.SUCCESS, 'Messege updated successfully.')
                return render(request, self.template_name,
                              {
                                  'form': form,
                                  'custom_precharge_message': m,
                              })
            else:
                return render(request, self.template_name,
                              {
                                  'form': form,
                                  'custom_precharge_message': m,
                              })
        else:
            form = CustomPreChargeMessageForm(request.POST)
            if form.is_valid():
                self.object = m = form.save()
                messages.add_message(self.request, messages.SUCCESS, 'Messege updated successfully.')
                return render(request, self.template_name,
                              {
                                  'form': form,
                              })
            else:
                return render(request, self.template_name, {'form': form})