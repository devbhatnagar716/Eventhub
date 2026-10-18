"""ASGI config for the eventhub project."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eventhub.settings')

application = get_asgi_application()
