"""Django ORM models for the EventHub ticketing API.

Two models, joined by a one-to-many relationship:

    Event 1 --- * Reservation

An Event tracks how many of its seats are still free (``available_seats``).
A Reservation records how many seats one attendee took (``seats_reserved``).

Invariant maintained by ``ReservationSerializer``:
    available_seats == total_seats - sum(seats_reserved of confirmed reservations)
"""

from django.db import models


class Event(models.Model):
    """A ticketed event at a venue on a given date."""

    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('ongoing', 'Ongoing'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    # Seats may only be reserved while the event is in one of these states.
    RESERVABLE_STATUSES = ('upcoming', 'ongoing')

    title = models.CharField(max_length=200)
    venue = models.CharField(max_length=200)
    date = models.DateField()
    total_seats = models.PositiveIntegerField()
    available_seats = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default='upcoming')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} at {self.venue}"

    class Meta:
        ordering = ['date']

    @property
    def seats_taken(self):
        """Seats already spoken for, i.e. not available to new reservations."""
        return self.total_seats - self.available_seats

    @property
    def is_reservable(self):
        return self.status in self.RESERVABLE_STATUSES


class Reservation(models.Model):
    """A booking of one or more seats on an Event."""

    STATUS_CHOICES = [
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name='reservations')
    attendee_name = models.CharField(max_length=200)
    attendee_email = models.EmailField()
    seats_reserved = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default='confirmed')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (f"{self.attendee_name} - {self.event.title} "
                f"({self.seats_reserved} seats)")

    class Meta:
        ordering = ['-created_at']
