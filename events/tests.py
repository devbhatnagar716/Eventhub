"""Automated cover for the assignment's testing checklist.

Run with:

    python manage.py test

Every test goes through the HTTP layer via DRF's APITestCase, so the router,
serializers, views, middleware and models are all exercised the same way a
Postman request would exercise them.
"""

import logging
import datetime

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Event, Reservation

EVENTS_URL = '/api/events/'
RESERVATIONS_URL = '/api/reservations/'


def events_url(pk=None):
    return EVENTS_URL if pk is None else f'{EVENTS_URL}{pk}/'


def reservations_url(pk=None):
    return RESERVATIONS_URL if pk is None else f'{RESERVATIONS_URL}{pk}/'


def cancel_url(pk):
    return f'{RESERVATIONS_URL}{pk}/cancel/'


class EventHubAPITestCase(APITestCase):
    """Shared fixtures: three events in different venues and statuses."""

    def setUp(self):
        self.upcoming = Event.objects.create(
            title='PyCon India 2026',
            venue='NIMHANS Convention Centre, Bangalore',
            date=datetime.date(2026, 11, 20),
            total_seats=500,
            available_seats=500,
            status='upcoming',
        )
        self.ongoing = Event.objects.create(
            title='Mumbai Jazz Night',
            venue='NCPA, Mumbai',
            date=datetime.date(2026, 10, 18),
            total_seats=100,
            available_seats=100,
            status='ongoing',
        )
        self.completed = Event.objects.create(
            title='Delhi Book Fair',
            venue='Pragati Maidan, Delhi',
            date=datetime.date(2026, 2, 12),
            total_seats=200,
            available_seats=50,
            status='completed',
        )
        self.valid_payload = {
            'event': self.upcoming.pk,
            'attendee_name': 'Priya Sharma',
            'attendee_email': 'priya@example.com',
            'seats_reserved': 2,
        }

    # -- helpers ------------------------------------------------------- #

    def seats_left(self, event=None):
        event = event or self.upcoming
        event.refresh_from_db()
        return event.available_seats

    def reserve(self, **overrides):
        payload = {**self.valid_payload, **overrides}
        return self.client.post(RESERVATIONS_URL, payload, format='json')


class EventEndpointTests(EventHubAPITestCase):
    """Checklist: create / list / filter events."""

    def test_create_event(self):
        payload = {
            'title': 'React Conf',
            'venue': 'Hyderabad International Convention Centre',
            'date': '2027-01-15',
            'total_seats': 300,
            'available_seats': 300,
            'status': 'upcoming',
        }
        response = self.client.post(EVENTS_URL, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Event.objects.count(), 4)
        self.assertEqual(response.data['title'], 'React Conf')
        self.assertEqual(response.data['reservations_count'], 0)

    def test_create_event_rejects_available_above_total(self):
        payload = {
            'title': 'Bad Data',
            'venue': 'Nowhere',
            'date': '2027-01-15',
            'total_seats': 10,
            'available_seats': 20,
        }
        response = self.client.post(EVENTS_URL, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('available_seats cannot exceed total_seats',
                      str(response.data))

    def test_list_events_is_ordered_by_date(self):
        response = self.client.get(EVENTS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)
        titles = [event['title'] for event in response.data]
        self.assertEqual(titles, [
            'Delhi Book Fair',        # 2026-02-12
            'Mumbai Jazz Night',      # 2026-10-18
            'PyCon India 2026',       # 2026-11-20
        ])

    def test_filter_events_by_status(self):
        response = self.client.get(EVENTS_URL, {'status': 'upcoming'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['title'], 'PyCon India 2026')

    def test_filter_events_by_venue_is_case_insensitive(self):
        response = self.client.get(EVENTS_URL, {'venue': 'mumbai'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['title'], 'Mumbai Jazz Night')

    def test_retrieve_single_event(self):
        response = self.client.get(events_url(self.upcoming.pk))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.upcoming.pk)
        self.assertEqual(response.data['available_seats'], 500)

    def test_update_event(self):
        response = self.client.put(
            events_url(self.upcoming.pk),
            {
                'title': 'PyCon India 2026 — Day 1',
                'venue': self.upcoming.venue,
                'date': '2026-11-20',
                'total_seats': 500,
                'available_seats': 500,
                'status': 'upcoming',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.upcoming.refresh_from_db()
        self.assertEqual(self.upcoming.title, 'PyCon India 2026 — Day 1')

    def test_partial_update_does_not_false_positive(self):
        """A PATCH sending only available_seats must not be compared to 0."""
        response = self.client.patch(
            events_url(self.upcoming.pk),
            {'available_seats': 480},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.upcoming.refresh_from_db()
        self.assertEqual(self.upcoming.available_seats, 480)

    def test_update_rejects_total_below_reserved(self):
        self.reserve(seats_reserved=10)  # 490 seats left

        response = self.client.patch(
            events_url(self.upcoming.pk),
            {'total_seats': 5},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already reserved', str(response.data))

    def test_delete_event_cascades_to_reservations(self):
        reservation = self.reserve().data

        response = self.client.delete(events_url(self.upcoming.pk))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            Reservation.objects.filter(pk=reservation['id']).exists()
        )


class ReservationEndpointTests(EventHubAPITestCase):
    """Checklist: create / overbook / retrieve / filter reservations."""

    def test_create_reservation_deducts_seats(self):
        response = self.reserve()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'confirmed')
        self.assertEqual(response.data['seats_reserved'], 2)
        self.assertEqual(self.seats_left(), 498)

    def test_reservations_count_tracks_confirmed_only(self):
        self.reserve()
        cancelled = self.reserve(attendee_email='raj@example.com').data
        self.client.post(cancel_url(cancelled['id']))

        response = self.client.get(events_url(self.upcoming.pk))

        self.assertEqual(response.data['reservations_count'], 1)

    def test_overbooking_returns_400(self):
        self.reserve(seats_reserved=499)  # 1 seat left
        response = self.reserve(seats_reserved=2)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            str(response.data['non_field_errors'][0]),
            'Only 1 seat(s) available.',
        )
        # Nothing was written: still exactly one reservation, one seat left.
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(self.seats_left(), 1)

    def test_zero_seats_rejected(self):
        response = self.reserve(seats_reserved=0)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Must reserve at least 1 seat', str(response.data))

    def test_negative_seats_rejected(self):
        response = self.reserve(seats_reserved=-3)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_completed_event_cannot_be_reserved(self):
        response = self.reserve(event=self.completed.pk)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Cannot reserve seats for a completed event',
                      str(response.data))

    def test_cancelled_event_cannot_be_reserved(self):
        self.completed.status = 'cancelled'
        self.completed.save(update_fields=['status'])

        response = self.reserve(event=self.completed.pk)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ongoing_event_can_be_reserved(self):
        response = self.reserve(event=self.ongoing.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.seats_left(self.ongoing), 98)

    def test_exactly_all_remaining_seats_is_allowed(self):
        response = self.reserve(seats_reserved=500)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.seats_left(), 0)

    def test_list_and_retrieve_reservations(self):
        created = self.reserve().data

        listing = self.client.get(RESERVATIONS_URL)
        detail = self.client.get(reservations_url(created['id']))

        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(len(listing.data), 1)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['attendee_email'], 'priya@example.com')

    def test_filter_reservations_by_event(self):
        self.reserve()
        self.reserve(event=self.ongoing.pk, attendee_email='raj@example.com')

        response = self.client.get(RESERVATIONS_URL,
                                   {'event_id': self.upcoming.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['event'], self.upcoming.pk)

    def test_reservation_cannot_be_created_with_cancelled_status(self):
        """status is read-only — sending cancelled on create is ignored."""
        payload = {**self.valid_payload, 'status': 'cancelled'}

        response = self.client.post(RESERVATIONS_URL, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'confirmed')

    def test_resize_down_restores_seats(self):
        reservation = self.reserve(seats_reserved=5).data
        self.assertEqual(self.seats_left(), 495)

        response = self.client.patch(
            reservations_url(reservation['id']),
            {'seats_reserved': 2},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.seats_left(), 498)

    def test_resize_up_beyond_availability_rejected(self):
        reservation = self.reserve(seats_reserved=5).data  # 495 left

        response = self.client.patch(
            reservations_url(reservation['id']),
            {'seats_reserved': 600},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.seats_left(), 495)

    def test_moving_reservation_between_events_moves_seats(self):
        reservation = self.reserve(seats_reserved=10).data  # up: 490

        response = self.client.patch(
            reservations_url(reservation['id']),
            {'event': self.ongoing.pk},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.seats_left(self.upcoming), 500)
        self.assertEqual(self.seats_left(self.ongoing), 90)

    def test_moving_reservation_into_a_full_event_is_rejected(self):
        reservation = self.reserve(seats_reserved=10).data  # up: 490
        self.reserve(event=self.ongoing.pk, seats_reserved=100)  # ongoing: 0

        response = self.client.patch(
            reservations_url(reservation['id']),
            {'event': self.ongoing.pk},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Nothing moved.
        self.assertEqual(self.seats_left(self.upcoming), 490)
        self.assertEqual(self.seats_left(self.ongoing), 0)


class CancelReservationTests(EventHubAPITestCase):
    """Checklist: cancel restores seats; cancelling twice is refused."""

    def test_cancel_restores_seats(self):
        reservation = self.reserve(seats_reserved=2).data
        self.assertEqual(self.seats_left(), 498)

        response = self.client.post(cancel_url(reservation['id']))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')
        self.assertEqual(self.seats_left(), 500)

    def test_cancel_twice_does_not_double_credit(self):
        reservation = self.reserve(seats_reserved=2).data

        first = self.client.post(cancel_url(reservation['id']))
        second = self.client.post(cancel_url(reservation['id']))

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.data['error'], 'Already cancelled.')
        self.assertEqual(self.seats_left(), 500)
        self.assertLessEqual(self.seats_left(), self.upcoming.total_seats)

    def test_cancelled_reservation_cannot_be_edited(self):
        reservation = self.reserve(seats_reserved=2).data
        self.client.post(cancel_url(reservation['id']))

        response = self.client.patch(
            reservations_url(reservation['id']),
            {'seats_reserved': 4},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.seats_left(), 500)

    def test_cancelling_an_unknown_reservation_returns_404(self):
        response = self.client.post(cancel_url(9999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class DeleteReservationTests(EventHubAPITestCase):
    """A hard delete must not strand the seats it was holding."""

    def test_delete_confirmed_reservation_restores_seats(self):
        reservation = self.reserve(seats_reserved=3).data
        self.assertEqual(self.seats_left(), 497)

        response = self.client.delete(reservations_url(reservation['id']))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            Reservation.objects.filter(pk=reservation['id']).exists()
        )
        self.assertEqual(self.seats_left(), 500)

    def test_delete_cancelled_reservation_does_not_credit_twice(self):
        reservation = self.reserve(seats_reserved=3).data
        self.client.post(cancel_url(reservation['id']))  # back to 500

        response = self.client.delete(reservations_url(reservation['id']))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.seats_left(), 500)
        self.assertLessEqual(self.seats_left(), self.upcoming.total_seats)

    def test_delete_unknown_reservation_returns_404(self):
        response = self.client.delete(reservations_url(9999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class MiddlewareTests(TestCase):
    """Checklist: middleware logs every request."""

    def test_request_is_logged_with_status_and_duration(self):
        with self.assertLogs('events.middleware', level=logging.INFO) as logs:
            self.client.get(EVENTS_URL)

        self.assertEqual(len(logs.output), 1)
        message = logs.output[0]
        self.assertIn('GET /api/events/', message)
        self.assertIn('200', message)
        self.assertRegex(message, r'\d+\.\d+s$')

    def test_logging_middleware_registered_in_settings(self):
        from django.conf import settings

        self.assertIn(
            'events.middleware.RequestLoggingMiddleware',
            settings.MIDDLEWARE,
        )
