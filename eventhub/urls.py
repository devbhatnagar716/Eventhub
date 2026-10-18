"""Root URL configuration for the eventhub project.

All API routes are registered on a DRF DefaultRouter inside ``events/urls.py``
and mounted here under ``/api/``.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('events.urls')),
]
