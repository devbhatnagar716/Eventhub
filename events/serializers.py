"""DRF serializers for EventHub.

Validation responsibilities live here, not in the views:

* ``EventSerializer``          — seat counts must be self-consistent.
* ``ReservationSerializer``    — a booking must be for a real number of seats,
                                 on an event that is still running, and within
                                 what is actually available.

Seat accounting writes (decrement on create, give-back on update) happen inside
``transaction.atomic()`` with the event row locked, so two simultaneous
requests for the last seat cannot both succeed.
"""

from django.db import transaction
from django.db.models import F
from rest_framework import serializers

from .models import Event, Reservation


class EventSerializer(serializers.ModelSerializer):
    """Event representation, including how many confirmed bookings it has."""

    reservations_count = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ['id', 'title', 'venue', 'date', 'total_seats',
                  'available_seats', 'status', 'created_at',
                  'reservations_count']

    def get_reservations_count(self, obj):
        return obj.reservations.filter(status='confirmed').count()

    def validate(self, data):
        """Keep total_seats and available_seats consistent with each other.

        The instance is used as the fallback for a PATCH that sends only one of
        the two fields — otherwise ``data.get('available_seats', 0)`` would
        compare a real value against a default of 0 and reject valid updates.
        """
        instance = self.instance

        total = data.get(
            'total_seats',
            instance.total_seats if instance else None,
        )
        available = data.get(
            'available_seats',
            instance.available_seats if instance else None,
        )

        # Missing fields are reported by the field-level validators.
        if total is None or available is None:
            return data

        # Shrinking total_seats must not strand seats that are already booked.
        # Checked first: it is the more specific diagnosis when both rules are
        # broken at once (e.g. total=5 while 10 seats are booked and 490 show
        # as available).
        if instance is not None and total < instance.seats_taken:
            raise serializers.ValidationError(
                f'total_seats cannot be less than the {instance.seats_taken} '
                f'seat(s) already reserved.'
            )

        if available > total:
            raise serializers.ValidationError(
                'available_seats cannot exceed total_seats.'
            )

        return data


class ReservationSerializer(serializers.ModelSerializer):
    """Reservation representation plus seat accounting on create/update."""

    class Meta:
        model = Reservation
        fields = ['id', 'event', 'attendee_name', 'attendee_email',
                  'seats_reserved', 'status', 'created_at']
        # Status changes go through the explicit cancel action, and
        # created_at is server-managed.
        read_only_fields = ['status', 'created_at']

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_seats_reserved(self, value):
        if value < 1:
            raise serializers.ValidationError('Must reserve at least 1 seat.')
        return value

    def validate(self, data):
        instance = self.instance

        if instance is not None and instance.status == 'cancelled':
            raise serializers.ValidationError(
                'A cancelled reservation cannot be modified.'
            )

        # On PATCH, `data` may omit either field — fall back to the instance.
        event = data.get('event', instance.event if instance else None)
        if event is None:
            # Required-field errors are raised by the field validators.
            return data

        if not event.is_reservable:
            raise serializers.ValidationError(
                f'Cannot reserve seats for a {event.status} event.'
            )

        seats = data.get(
            'seats_reserved',
            instance.seats_reserved if instance else 0,
        )

        # Seats this reservation already holds are available to it again, so a
        # resize is judged against the event's free seats plus its own hold.
        available = event.available_seats
        if instance is not None and instance.event_id == event.pk:
            available += instance.seats_reserved

        if seats > available:
            raise serializers.ValidationError(
                f'Only {available} seat(s) available.'
            )

        return data

    # ------------------------------------------------------------------ #
    # Writes
    #
    # Both the reservation row and the event's seat counter are written in one
    # transaction: if either write fails, neither is kept.
    # ------------------------------------------------------------------ #

    def create(self, validated_data):
        event = validated_data['event']
        seats = validated_data['seats_reserved']

        with transaction.atomic():
            # Lock the event row so two concurrent requests for the same seat
            # are serialised. Re-read the count under the lock — the value
            # checked during validation may already be stale.
            event = Event.objects.select_for_update().get(pk=event.pk)

            if seats > event.available_seats:
                raise serializers.ValidationError({
                    'non_field_errors': [
                        f'Only {event.available_seats} seat(s) available.'
                    ]
                })

            event.available_seats = F('available_seats') - seats
            event.save(update_fields=['available_seats'])

            # validated_data already carries an 'event' key, so swap in the
            # locked instance rather than passing it twice.
            data = {**validated_data, 'event': event}
            reservation = Reservation.objects.create(**data)

        return reservation

    def update(self, instance, validated_data):
        new_event = validated_data.get('event', instance.event)
        new_seats = validated_data.get('seats_reserved', instance.seats_reserved)

        with transaction.atomic():
            # Lock both events in a stable order (by primary key) so two
            # cross-moving updates cannot deadlock each other.
            locked = {
                event.pk: event
                for event in Event.objects.select_for_update()
                .filter(pk__in={instance.event_id, new_event.pk})
                .order_by('pk')
            }
            old_event = locked[instance.event_id]
            target_event = locked[new_event.pk]

            # Hand back the seats this reservation was holding, then take the
            # new count. When the event is unchanged this becomes a net delta
            # on a single row.
            old_event.available_seats += instance.seats_reserved

            if new_seats > target_event.available_seats:
                raise serializers.ValidationError({
                    'non_field_errors': [
                        f'Only {target_event.available_seats} seat(s) available.'
                    ]
                })

            target_event.available_seats -= new_seats
            target_event.save(update_fields=['available_seats'])
            if target_event.pk != old_event.pk:
                old_event.save(update_fields=['available_seats'])

            instance.event = target_event
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()

        return instance
