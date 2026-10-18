from django.contrib import admin

from .models import Event, Reservation


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'venue', 'date', 'status',
                    'total_seats', 'available_seats')
    list_filter = ('status', 'date')
    search_fields = ('title', 'venue')


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('attendee_name', 'attendee_email', 'event',
                    'seats_reserved', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('attendee_name', 'attendee_email')
