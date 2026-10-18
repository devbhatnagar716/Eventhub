"""Router configuration for the events app.

Mounted at ``/api/`` by ``eventhub/urls.py``, so the registered prefixes
produce ``/api/events/`` and ``/api/reservations/``.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import EventViewSet, ReservationViewSet

router = DefaultRouter()
router.register(r'events', EventViewSet, basename='event')
router.register(r'reservations', ReservationViewSet, basename='reservation')

urlpatterns = [
    path('', include(router.urls)),
]
