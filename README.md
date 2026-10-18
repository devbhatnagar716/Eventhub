# EventHub

A backend API for a simplified event ticketing platform, built with Django and
Django REST Framework. Users can browse events, reserve seats, cancel
reservations, and filter both events and reservations through query parameters.

## Stack

- Python 3.10+
- Django 4.2
- Django REST Framework 3.15
- SQLite (default file database, no external service needed)

## How to run

```bash
# 1. Clone and enter the project
git clone <your-repo-url>
cd eventhub

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Apply migrations (creates db.sqlite3)
python manage.py makemigrations
python manage.py migrate

# 5. Start the development server
python manage.py runserver
```

The API is served at `http://127.0.0.1:8000/api/`. The browsable DRF interface
is available in a regular browser; the Django admin sits at `/admin/`.

Run the test suite with:

```bash
python manage.py test
```

It covers the full manual testing checklist: creating, listing and filtering
events, creating a reservation, the overbooking rejection, cancellation,
filtering reservations by `event_id`, and the middleware log line.

Two offline checks are included as well, for machines where the dependencies
cannot be installed. `verify_logic.py` stubs the Django/DRF surface and drives
the real serializers, views and middleware against an in-memory store;
`verify_migration.py` compares `0001_initial.py` against `models.py` field by
field, standing in for `makemigrations --check`. Neither is a replacement for
`manage.py test`.

```bash
python verify_logic.py                       # 64 assertions
python verify_migration.py                   # migration drift check
```

## Endpoints

All endpoints are unauthenticated and accept/return JSON.

### Events

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/events/` | List all events (ordered by `date`) |
| POST | `/api/events/` | Create an event |
| GET | `/api/events/{id}/` | Get a single event |
| PUT | `/api/events/{id}/` | Update an event |
| PATCH | `/api/events/{id}/` | Partially update an event |
| DELETE | `/api/events/{id}/` | Delete an event (cascades to its reservations) |
| GET | `/api/events/?status=upcoming` | Filter by status (`upcoming`, `ongoing`, `completed`, `cancelled`) |
| GET | `/api/events/?venue=mumbai` | Filter by venue, case-insensitive substring |

Each event also returns `reservations_count`: the number of **confirmed**
reservations it has.

### Reservations

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/reservations/` | List all reservations (newest first) |
| POST | `/api/reservations/` | Create a reservation and deduct seats |
| GET | `/api/reservations/{id}/` | Get a single reservation |
| PUT | `/api/reservations/{id}/` | Update a reservation |
| PATCH | `/api/reservations/{id}/` | Partially update a reservation |
| DELETE | `/api/reservations/{id}/` | Delete a reservation, returning its seats if it was confirmed |
| GET | `/api/reservations/?event_id=1` | Filter reservations by event |
| POST | `/api/reservations/{id}/cancel/` | Cancel a reservation and restore its seats |

`status` and `created_at` are read-only on reservations — `status` changes only
through the `cancel` action.

### Example requests

Create an event:

```bash
curl -X POST http://127.0.0.1:8000/api/events/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "PyCon India 2026",
    "venue": "NIMHANS Convention Centre, Bangalore",
    "date": "2026-11-20",
    "total_seats": 500,
    "available_seats": 500,
    "status": "upcoming"
  }'
```

Reserve two seats (`201 Created`, and the event's `available_seats` drops by 2):

```bash
curl -X POST http://127.0.0.1:8000/api/reservations/ \
  -H "Content-Type: application/json" \
  -d '{
    "event": 1,
    "attendee_name": "Priya Sharma",
    "attendee_email": "priya@example.com",
    "seats_reserved": 2
  }'
```

Overbooking (`400 Bad Request`):

```json
{"non_field_errors": ["Only 1 seat(s) available."]}
```

Cancel (`200 OK`, seats restored):

```bash
curl -X POST http://127.0.0.1:8000/api/reservations/1/cancel/
```

## Postman

`EventHub.postman_collection.json` can be imported directly into Postman. It
contains every endpoint plus a ready-made sequence for the three required
screenshots:

1. **Successful reservation** — creates an event, then books 2 of its seats and
   asserts `201 Created`.
2. **Overbooking failure** — books all but one seat, then requests two more and
   asserts `400 Bad Request`.
3. **Successful cancellation** — cancels the reservation from step 1 and asserts
   `200 OK` with `status: "cancelled"`.

The "Screenshot Scenarios" folder is meant to be run top to bottom; each request
saves the IDs it needs into collection variables, so no manual copying is
required.

## Project structure

```
eventhub/
├── manage.py
├── requirements.txt
├── eventhub/
│   ├── settings.py        # DRF, logging config, middleware registration
│   └── urls.py            # mounts the router under /api/
├── events/
│   ├── models.py          # Event, Reservation
│   ├── serializers.py     # validation + seat accounting
│   ├── views.py           # EventViewSet, ReservationViewSet
│   ├── urls.py            # DefaultRouter registration
│   ├── middleware.py      # RequestLoggingMiddleware
│   ├── admin.py
│   ├── tests.py
│   └── migrations/
│       └── 0001_initial.py
└── db.sqlite3
```

## Middleware

`RequestLoggingMiddleware` logs one line per request to the terminal:

```
[2026-10-18 10:00:00,123] INFO events.middleware - POST /api/reservations/ - 201 - 0.03s
```

The `events` logger is configured explicitly in `settings.py` with an INFO
console handler. Django's built-in logging config only wires up `django.*`
loggers, so without that entry the middleware's INFO records would be silently
discarded.

## Design decision: seat deduction is locked, not just validated

The handout deducts seats in `ReservationSerializer.create()`. The obvious
version of that method reads `event.available_seats`, subtracts, and saves. That
is correct for one request at a time, but a ticketing platform is defined by the
case where it is not: two users hit *Reserve* for the last seat in the same
instant. Both requests read `available_seats = 1`, both pass validation, both
write `0`, and the venue sells two seats it does not have.

So the seat write happens inside a transaction that holds a row lock on the
event:

```python
with transaction.atomic():
    event = Event.objects.select_for_update().get(pk=event.pk)

    if seats > event.available_seats:          # re-checked under the lock
        raise serializers.ValidationError({...})

    event.available_seats = F('available_seats') - seats
    event.save(update_fields=['available_seats'])
    reservation = Reservation.objects.create(**{**validated_data, 'event': event})
```

Three things matter here. The availability check is **repeated after the lock
is acquired**, because the count that validation saw may already be stale. The
decrement uses an **`F()` expression**, so the database does the arithmetic on
the value it holds rather than on a Python integer read a moment earlier. And
the reservation insert is in the **same transaction**, so a failure after the
decrement rolls the seats back instead of leaking them.

The same reasoning applies to the other two write paths. Cancelling locks both
the event and the reservation, so a double-tap on *Cancel* cannot credit the
same seats twice — the second call sees `status='cancelled'` inside the lock and
returns 400. Deleting a reservation is a hard delete, so `destroy()` gives a
confirmed reservation's seats back before removing the row; without that, a
plain `DELETE` would destroy the only record of the hold and leak those seats
out of the event's inventory for good. Updating a reservation's seat count gives
the old hold back before taking the new one, and locks both events in
primary-key order so two reservations being swapped between the same pair of
events cannot deadlock.

Validation also guards the state the models cannot: an event's
`available_seats` may not exceed its `total_seats`, `total_seats` may not be
shrunk below the seats already booked, bookings are refused for `completed` or
`cancelled` events, and `seats_reserved` must be at least 1.

### A note on SQLite

SQLite ignores `SELECT ... FOR UPDATE` — it has no row-level locking, and
instead serialises writers across the whole database file. Locally, two truly
simultaneous bookings therefore resolve one of two ways: one request waits and
then sees the updated count, or it fails with `database is locked`. Either way a
seat is not sold twice, but the mechanism is SQLite's file lock rather than the
explicit row lock. On PostgreSQL or MySQL — where `select_for_update()` does
what it says — the `atomic()` block above is what serialises the two requests,
and it is the version worth describing in an interview.

## Author

Dev Bhatnagar
