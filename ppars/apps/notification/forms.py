from django import forms

from ppars.apps.notification.models import SpamMessage, CustomPreChargeMessage


class SpamMessageForm(forms.ModelForm):
    class Meta:
        model = SpamMessage
        fields = ['message', 'customer_type', 'send_with']


class CustomPreChargeMessageForm(forms.ModelForm):
    class Meta:
        model = CustomPreChargeMessage
