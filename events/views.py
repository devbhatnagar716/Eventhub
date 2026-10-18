"""ViewSets for the EventHub API.

Filtering happens in ``get_queryset()`` by reading query parameters, which is
the same pattern used for filtering applications by ``job_id``. Both viewsets
are registered on a ``DefaultRouter`` in ``events/urls.py``.
"""

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Event, Reservation
from .serializers import EventSerializer, ReservationSerializer


class EventViewSet(viewsets.ModelViewSet):
    """CRUD for events, filterable by ``status`` and ``venue``.

        GET /api/events/                  list all events
        GET /api/events/?status=upcoming  filter by status (exact match)
        GET /api/events/?venue=mumbai     filter by venue (case-insensitive)
        POST /api/events/                 create an event
        GET /api/events/{id}/             retrieve one event
        PUT/PATCH /api/events/{id}/       update an event
        DELETE /api/events/{id}/          delete an event
    """

    queryset = Event.objects.all()
    serializer_class = EventSerializer

    def get_queryset(self):
        queryset = Event.objects.all()

        status_param = self.request.query_params.get('status')
        venue_param = self.request.query_params.get('venue')

        if status_param:
            queryset = queryset.filter(status=status_param)
        if venue_param:
            queryset = queryset.filter(venue__icontains=venue_param)

        return queryset


class ReservationViewSet(viewsets.ModelViewSet):
    """CRUD for reservations, filterable by ``event_id``, with a cancel action.

        GET /api/reservations/                    list all reservations
        GET /api/reservations/?event_id=1         filter by event
        POST /api/reservations/                   create a reservation
        GET /api/reservations/{id}/               retrieve one reservation
        PUT/PATCH /api/reservations/{id}/         update a reservation
        DELETE /api/reservations/{id}/            delete a reservation
        POST /api/reservations/{id}/cancel/       cancel (used by the frontend)
    """

    queryset = Reservation.objects.all()
    serializer_class = ReservationSerializer

    def get_queryset(self):
        queryset = Reservation.objects.all()

        event_id = self.request.query_params.get('event_id')
        if event_id:
            queryset = queryset.filter(event_id=event_id)

        return queryset

    # detail=True means the route is /api/reservations/{id}/cancel/
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a reservation and return its seats to the event."""
        reservation = self.get_object()

        if reservation.status == 'cancelled':
            return Response(
                {'error': 'Already cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Lock the event row so the restored count cannot race with a
            # concurrent booking, and so a double-tap on Cancel cannot credit
            # the same seats twice.
            event = Event.objects.select_for_update().get(pk=reservation.event_id)
            reservation = Reservation.objects.select_for_update().get(
                pk=reservation.pk
            )

            if reservation.status == 'cancelled':
                return Response(
                    {'error': 'Already cancelled.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            event.available_seats += reservation.seats_reserved
            event.save(update_fields=['available_seats'])

            reservation.status = 'cancelled'
            reservation.save(update_fields=['status'])

        return Response(self.get_serializer(reservation).data)

    def destroy(self, request, *args, **kwargs):
        """Hard delete, but do not strand the seats a live reservation holds.

        Without this, DELETE /api/reservations/{id}/ would remove the row and
        leave its seats deducted from the event forever — the counter has no
        other record to reconcile against.
        """
        reservation = self.get_object()

        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=reservation.event_id)
            reservation = Reservation.objects.select_for_update().get(
                pk=reservation.pk
            )

            if reservation.status == 'confirmed':
                event.available_seats += reservation.seats_reserved
                event.save(update_fields=['available_seats'])

            reservation.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
