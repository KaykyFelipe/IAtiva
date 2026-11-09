from django import forms
from django.conf import settings


class MessageForm(forms.Form):
    mensagem = forms.CharField(
        label="Mensagem",
        max_length=settings.IATIVA_CHAT_MAX_CHARS,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3, "autocomplete": "off", "spellcheck": "true"}),
    )
