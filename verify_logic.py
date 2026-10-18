"""Logic harness for EventHub (verification only — not part of the deliverable).

Django and DRF cannot be pip-installed in this sandbox, so this script stubs
just enough of them to import and execute the *real* serializers.py /
views.py / middleware.py against an in-memory store.

It re-checks the same scenarios as events/tests.py. It is NOT a substitute for
`manage.py test` on a machine with Django installed.
"""

import datetime
import logging
import os
import sys
import types


# ---------------------------------------------------------------- stubs --- #

class Field:
    def __init__(self, *a, **kw):
        self.kwargs = kw

    def run_validation(self, value):
        return value


models_mod = types.ModuleType('django.db.models')
for _name in ('CharField', 'EmailField', 'DateField', 'IntegerField',
              'PositiveIntegerField', 'DateTimeField', 'ForeignKey',
              'BigAutoField', 'AutoField'):
    setattr(models_mod, _name, Field)


class Model:
    pass


models_mod.Model = Model
models_mod.CASCADE = 'CASCADE'


class F:
    """Minimal F() expression supporting ``F('x') - n``."""

    def __init__(self, name, delta=0):
        self.name = name
        self.delta = delta

    def __sub__(self, other):
        return F(self.name, self.delta - other)


models_mod.F = F


class Atomic:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


db_mod = types.ModuleType('django.db')
db_mod.models = models_mod
db_mod.transaction = types.SimpleNamespace(atomic=lambda: Atomic())

django_mod = types.ModuleType('django')
django_mod.db = db_mod
sys.modules['django'] = django_mod
sys.modules['django.db'] = db_mod
sys.modules['django.db.models'] = models_mod


class ValidationError(Exception):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(str(detail))


class SerializerMethodField:
    def __init__(self, *a, **kw):
        self.kwargs = kw


class _BaseSerializer:
    def __init__(self, instance=None, data=None, partial=False, **kw):
        self.instance = instance
        self._data = data
        self._partial = partial
        self._validated = {}
        self._errors = {}

    def to_representation(self):
        obj = self.instance
        out = {}
        for name in self.Meta.fields:
            attr = getattr(type(self), name, None)
            if isinstance(attr, SerializerMethodField):
                getter = getattr(self, f'get_{name}', None)
                out[name] = getter(obj) if getter else None
            elif name in ('event', 'event_id'):
                out[name] = obj.event.pk
            elif name == 'id':
                out[name] = obj.pk
            elif name == 'date':
                out[name] = obj.date.isoformat()
            elif name == 'created_at':
                out[name] = getattr(obj, 'created_at', None)
            else:
                out[name] = getattr(obj, name)
        return out

    @property
    def data(self):
        return self.to_representation()

    def run_validation(self, data):
        self._validated = {}
        self._errors = {}
        partial = self._partial
        read_only = set(getattr(self.Meta, 'read_only_fields', ()))
        # id: AutoField is read-only. created_at: auto_now_add implies read-only.
        read_only.update({'id', 'created_at'})

        # Model-level defaults (e.g. status='upcoming') are optional on create.
        defaults = {}
        for name, default in getattr(self.Meta, 'defaults', {}).items():
            defaults[name] = default

        for name in self.Meta.fields:
            if name in read_only or isinstance(
                    getattr(type(self), name, None), SerializerMethodField):
                continue
            if name not in data:
                if name in defaults:
                    self._validated[name] = defaults[name]
                elif not partial:
                    self._errors[name] = ['This field is required.']
                continue
            value = data[name]
            if name == 'event':
                # PrimaryKeyRelatedField: resolve pk -> instance, 400 if absent.
                match = Event.objects.filter(pk=value)
                if not match:
                    self._errors[name] = [f'Invalid pk "{value}" - object does not exist.']
                    continue
                value = match[0]
            hook = getattr(self, f'validate_{name}', None)
            if hook:
                try:
                    value = hook(value)
                except ValidationError as exc:
                    self._errors[name] = [exc.detail]
                    continue
            self._validated[name] = value

        if self._errors:
            return False

        if hasattr(self, 'validate'):
            try:
                self._validated = self.validate(self._validated)
            except ValidationError as exc:
                detail = exc.detail
                if isinstance(detail, dict):
                    self._errors = detail
                else:
                    self._errors = {'non_field_errors': [detail]}
                return False

        return True

    def save(self):
        if self.instance is None:
            self.instance = self.create(self._validated)
        else:
            self.instance = self.update(self.instance, self._validated)
        return self.instance

    # Default ModelSerializer behaviour for serializers that do not override
    # these (EventSerializer relies on it; ReservationSerializer defines both).
    def create(self, validated_data):
        manager = self.Meta.model.objects
        return manager.create(**validated_data)

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


serializers_mod = types.ModuleType('rest_framework.serializers')
serializers_mod.Serializer = _BaseSerializer
serializers_mod.ModelSerializer = _BaseSerializer
serializers_mod.ValidationError = ValidationError
serializers_mod.SerializerMethodField = SerializerMethodField
for _name in ('CharField', 'EmailField', 'DateField', 'IntegerField',
              'PositiveIntegerField', 'DateTimeField'):
    setattr(serializers_mod, _name, Field)


class Response:
    def __init__(self, data=None, status=None):
        self.data = data
        self.status_code = 200 if status is None else status  # DRF default


class Action:
    def __init__(self, detail=False, methods=None):
        self.detail = detail
        self.methods = methods

    def __call__(self, fn):
        fn._action = self
        return fn


class ViewSet:
    def destroy(self, request, *args, **kwargs):
        """DRF default destroy(): delete the object, return 204."""
        self.get_object().delete()
        return Response(status=204)


rest_mod = types.ModuleType('rest_framework')
rest_mod.serializers = serializers_mod
rest_mod.status = types.SimpleNamespace(
    HTTP_200_OK=200, HTTP_201_CREATED=201, HTTP_204_NO_CONTENT=204,
    HTTP_400_BAD_REQUEST=400, HTTP_404_NOT_FOUND=404,
)
rest_mod.viewsets = types.ModuleType('rest_framework.viewsets')
rest_mod.viewsets.ModelViewSet = ViewSet
rest_mod.decorators = types.ModuleType('rest_framework.decorators')
rest_mod.decorators.action = Action
rest_mod.response = types.ModuleType('rest_framework.response')
rest_mod.response.Response = Response

sys.modules['rest_framework'] = rest_mod
sys.modules['rest_framework.serializers'] = serializers_mod
sys.modules['rest_framework.viewsets'] = rest_mod.viewsets
sys.modules['rest_framework.decorators'] = rest_mod.decorators
sys.modules['rest_framework.response'] = rest_mod.response


# ---------------------------------------------------------------- store --- #

class Store:
    def __init__(self):
        self.events = {}
        self.reservations = {}
        self.next_event = 1
        self.next_res = 1


STORE = Store()


class QuerySet(list):
    def filter(self, **kw):
        out = QuerySet(self)
        for key, val in kw.items():
            if key == 'pk__in':
                out = QuerySet(r for r in out if r.pk in val)
            elif key.endswith('__icontains'):
                field = key[:-len('__icontains')]
                out = QuerySet(r for r in out
                               if val.lower() in getattr(r, field).lower())
            elif key == 'event_id':
                out = QuerySet(r for r in out if r.event_id == val)
            elif key == 'status':
                out = QuerySet(r for r in out if r.status == val)
            else:
                out = QuerySet(r for r in out if getattr(r, key) == val)
        return out

    def get(self, **kw):
        found = self.filter(**kw)
        if len(found) != 1:
            raise LookupError('DoesNotExist' if not found else 'Multiple')
        # Fresh instance per fetch, as Django does (no shared aliasing).
        row = {k: v for k, v in vars(found[0]).items() if not k.startswith('_')}
        return type(found[0])(_skip_store=True, **row)

    def select_for_update(self):
        return self

    def order_by(self, *fields):
        for field in reversed(fields):
            reverse = field.startswith('-')
            key = field.lstrip('-')
            self.sort(key=lambda r: getattr(r, key), reverse=reverse)
        return self

    def count(self):
        return len(self)

    def create(self, **data):
        raise NotImplementedError


class EventManager(QuerySet):
    _model = None

    def __get__(self, instance, owner):
        EventManager._model = owner
        return self

    def all(self):
        return QuerySet(STORE.events.values()).order_by('date')

    def get(self, **kw):
        return self.all().get(**kw)

    def filter(self, **kw):
        return self.all().filter(**kw)

    def select_for_update(self):
        return self.all()

    def create(self, **data):
        event = Event(_skip_store=True, **data)
        if event.pk is None:
            event.pk = STORE.next_event
            STORE.next_event += 1
        STORE.events[event.pk] = event
        return event


class ReservationManager(QuerySet):
    _model = None

    def __get__(self, instance, owner):
        ReservationManager._model = owner
        return self

    def all(self):
        return QuerySet(STORE.reservations.values()).order_by('-created_at')

    def get(self, **kw):
        return self.all().get(**kw)

    def filter(self, **kw):
        return self.all().filter(**kw)

    def select_for_update(self):
        return self.all()

    def create(self, **data):
        res = Reservation(_skip_store=True, **data)
        res.pk = STORE.next_res
        STORE.next_res += 1
        if 'status' not in data:
            res.status = 'confirmed'
        res.created_at = datetime.datetime.now()
        STORE.reservations[res.pk] = res
        return res


class BaseModel:
    def __init__(self, _skip_store=False, **data):
        for key, val in data.items():
            if not isinstance(val, F):
                setattr(self, key, val)
        if not _skip_store:
            raise RuntimeError('use manager.create()')

    def save(self, update_fields=None):
        if isinstance(self, Event):
            if isinstance(self.available_seats, F):
                # Resolve against the live row, as the database would.
                live = STORE.events[self.pk]
                self.available_seats = live.available_seats + self.available_seats.delta
            STORE.events[self.pk] = self
        else:
            STORE.reservations[self.pk] = self

    def refresh_from_db(self, fields=None):
        bank = STORE.events if isinstance(self, Event) else STORE.reservations
        live = bank[self.pk]
        for field in (fields or list(vars(live))):
            if hasattr(live, field):
                setattr(self, field, getattr(live, field))

    def delete(self):
        if isinstance(self, Event):
            for res in list(STORE.reservations.values()):
                if res.event_id == self.pk:
                    del STORE.reservations[res.pk]
            del STORE.events[self.pk]
        else:
            STORE.reservations.pop(self.pk, None)


class Event(BaseModel):
    objects = EventManager()
    RESERVABLE_STATUSES = ('upcoming', 'ongoing')

    def __init__(self, **kw):
        kw.setdefault('_skip_store', True)
        kw.setdefault('status', 'upcoming')
        kw.setdefault('pk', None)
        super().__init__(**kw)

    @property
    def seats_taken(self):
        return self.total_seats - self.available_seats

    @property
    def is_reservable(self):
        return self.status in self.RESERVABLE_STATUSES

    @property
    def reservations(self):
        """Reverse FK manager (related_name='reservations')."""
        return QuerySet(r for r in STORE.reservations.values()
                        if r.event_id == self.pk)

    def __str__(self):
        return f"{self.title} at {self.venue}"


class Reservation(BaseModel):
    objects = ReservationManager()

    def __init__(self, **kw):
        kw.setdefault('_skip_store', True)
        kw.setdefault('pk', None)
        super().__init__(**kw)

    @property
    def event(self):
        return STORE.events[self.event_id]

    @event.setter
    def event(self, value):
        self.event_id = value.pk

    def __str__(self):
        return f"{self.attendee_name} ({self.seats_reserved} seats)"


SRC = os.environ['EVENTHUB_SRC']
sys.path.insert(0, SRC)

# Replace events.models with the in-memory stub so serializers.py / views.py
# bind to it instead of the real Django models.
_stub_models = types.ModuleType('events.models')
_stub_models.Event = Event
_stub_models.Reservation = Reservation
sys.modules['events.models'] = _stub_models

from events import serializers as S  # noqa: E402
from events import views as V  # noqa: E402
from events import middleware as M  # noqa: E402

S.Event = Event
S.Reservation = Reservation
V.Event = Event
V.Reservation = Reservation

# Model-level defaults, which the real DRF reads from the model fields.
S.EventSerializer.Meta.defaults = {'status': 'upcoming'}
S.EventSerializer.Meta.model = Event
S.ReservationSerializer.Meta.model = Reservation


class NotFound(Exception):
    pass


def _get_object(self):
    """Stand-in for GenericAPIView.get_object(): 404 when the pk is absent."""
    try:
        return self.get_queryset().get(pk=self._pk)
    except LookupError:
        raise NotFound()


def _get_serializer(self, instance=None, **kw):
    """Stand-in for GenericAPIView.get_serializer()."""
    return self.serializer_class(instance=instance, **kw)


V.EventViewSet.get_object = _get_object
V.EventViewSet.get_serializer = _get_serializer
V.ReservationViewSet.get_object = _get_object
V.ReservationViewSet.get_serializer = _get_serializer


# --------------------------------------------------------------- client --- #

class Request:
    def __init__(self, method, path, query=None, body=None):
        self.method = method
        self.path = path
        self.query_params = query or {}
        self.body = body or {}


def call(method, path, query=None, body=None, pk=None, action=None):
    request = Request(method, path, query, body)

    if path.startswith('/api/events'):
        viewset = V.EventViewSet()
        viewset.request = request
        model, serializer_cls = Event, S.EventSerializer
    else:
        viewset = V.ReservationViewSet()
        viewset.request = request
        model, serializer_cls = Reservation, S.ReservationSerializer

    if action == 'cancel':
        viewset._pk = pk
        try:
            return viewset.cancel(request, pk=pk)
        except NotFound:
            return Response({'detail': 'Not found.'}, 404)

    if pk is None:
        # DRF resolves the detail pk from the URL; do the same here.
        parts = [p for p in path.rstrip('/').split('/') if p]
        if parts and parts[-1].isdigit():
            pk = int(parts[-1])

    if method == 'GET' and pk is None:
        return Response([serializer_cls(q).data
                         for q in viewset.get_queryset()], 200)

    if method == 'GET':
        try:
            obj = model.objects.get(pk=pk)
        except LookupError:
            return Response({'detail': 'Not found'}, 404)
        return Response(serializer_cls(obj).data, 200)

    if method == 'DELETE':
        # Route through destroy(), as ModelViewSet does.
        viewset._pk = pk
        try:
            return viewset.destroy(request, pk=pk)
        except NotFound:
            return Response({'detail': 'Not found.'}, 404)

    instance = None
    if pk is not None:
        try:
            instance = model.objects.get(pk=pk)
        except LookupError:
            return Response({'detail': 'Not found'}, 404)

    ser = serializer_cls(instance=instance, data=body, partial=(method == 'PATCH'))
    if not ser.run_validation(body):
        return Response(ser._errors, 400)
    obj = ser.save()
    return Response(serializer_cls(obj).data, 201 if instance is None else 200)


# --------------------------------------------------------------- checks --- #

PASS, FAIL = [], []


def check(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    if not condition:
        print(f'  FAIL  {name}   {detail}')


def fresh():
    STORE.__init__()
    up = Event(title='PyCon India 2026',
               venue='NIMHANS Convention Centre, Bangalore',
               date=datetime.date(2026, 11, 20),
               total_seats=500, available_seats=500, status='upcoming')
    up.pk = STORE.next_event
    STORE.next_event += 1
    STORE.events[up.pk] = up

    ong = Event(title='Mumbai Jazz Night', venue='NCPA, Mumbai',
                date=datetime.date(2026, 10, 18),
                total_seats=100, available_seats=100, status='ongoing')
    ong.pk = STORE.next_event
    STORE.next_event += 1
    STORE.events[ong.pk] = ong

    done = Event(title='Delhi Book Fair', venue='Pragati Maidan, Delhi',
                 date=datetime.date(2026, 2, 12),
                 total_seats=200, available_seats=50, status='completed')
    done.pk = STORE.next_event
    STORE.next_event += 1
    STORE.events[done.pk] = done
    return up, ong, done


def seats(event):
    return STORE.events[event.pk].available_seats


def reserve(event, seats_n=2, **over):
    body = {'event': event.pk, 'attendee_name': 'Priya Sharma',
            'attendee_email': 'priya@example.com', 'seats_reserved': seats_n}
    body.update(over)
    return call('POST', '/api/reservations', body=body)


print('=== events ===')
up, ong, done = fresh()
r = call('POST', '/api/events', body={
    'title': 'React Conf', 'venue': 'HICC', 'date': datetime.date(2027, 1, 15),
    'total_seats': 300, 'available_seats': 300, 'status': 'upcoming'})
check('create event -> 201', r.status_code == 201, r.data)
check('reservations_count present', 'reservations_count' in r.data, r.data)

r = call('POST', '/api/events', body={
    'title': 'Bad', 'venue': 'X', 'date': datetime.date(2027, 1, 1),
    'total_seats': 10, 'available_seats': 20})
check('available>total -> 400', r.status_code == 400, r.data)
check('available>total message',
      'available_seats cannot exceed total_seats' in str(r.data), r.data)

up, ong, done = fresh()
r = call('GET', '/api/events')
check('list ordered by date',
      [e['title'] for e in r.data] ==
      ['Delhi Book Fair', 'Mumbai Jazz Night', 'PyCon India 2026'],
      [e['title'] for e in r.data])

r = call('GET', '/api/events', query={'status': 'upcoming'})
check('filter by status', len(r.data) == 1 and r.data[0]['title'].startswith('PyCon'),
      r.data)

r = call('GET', '/api/events', query={'venue': 'mumbai'})
check('filter by venue icontains',
      len(r.data) == 1 and r.data[0]['title'] == 'Mumbai Jazz Night', r.data)

r = call('GET', f'/api/events/{up.pk}/')
check('retrieve event', r.status_code == 200 and r.data['id'] == up.pk, r.data)

r = call('PATCH', f'/api/events/{up.pk}/', body={'available_seats': 480})
check('PATCH one seat field not false-positived', r.status_code == 200, r.data)
check('PATCH applied', seats(up) == 480, seats(up))

up, ong, done = fresh()
reserve(up, 10)
r = call('PATCH', f'/api/events/{up.pk}/', body={'total_seats': 5})
check('shrink total below booked -> 400', r.status_code == 400, r.data)
check('shrink message', 'already reserved' in str(r.data), r.data)
check('failed shrink changed nothing', seats(up) == 490, seats(up))

up, ong, done = fresh()
res = reserve(up).data
r = call('DELETE', f'/api/events/{up.pk}/')
check('delete event -> 204', r.status_code == 204)
check('cascade removed reservations', res['id'] not in STORE.reservations)

print('=== reservations ===')
up, ong, done = fresh()
r = reserve(up, 2)
check('create reservation -> 201', r.status_code == 201, r.data)
check('status confirmed', r.data.get('status') == 'confirmed', r.data)
check('seats deducted 500->498', seats(up) == 498, seats(up))
check('response has created_at', 'created_at' in r.data, r.data)

up, ong, done = fresh()
reserve(up, 499)
r = reserve(up, 2)
check('overbooking -> 400', r.status_code == 400, r.data)
check('overbooking message exact',
      r.data.get('non_field_errors') and
      str(r.data['non_field_errors'][0]) == 'Only 1 seat(s) available.', r.data)
check('overbooking wrote nothing', len(STORE.reservations) == 1, len(STORE.reservations))
check('overbooking left 1 seat', seats(up) == 1, seats(up))

up, ong, done = fresh()
r = reserve(up, 0)
check('zero seats -> 400', r.status_code == 400, r.data)
check('zero seats message', 'Must reserve at least 1 seat' in str(r.data), r.data)
check('negative seats -> 400', reserve(up, -3).status_code == 400)

up, ong, done = fresh()
r = reserve(done)
check('completed event -> 400', r.status_code == 400, r.data)
check('completed message',
      'Cannot reserve seats for a completed event' in str(r.data), r.data)

up, ong, done = fresh()
r = reserve(ong)
check('ongoing event allowed', r.status_code == 201, r.data)
check('ongoing seats deducted', seats(ong) == 98, seats(ong))

up, ong, done = fresh()
check('exactly all seats allowed', reserve(up, 500).status_code == 201)
check('zero left', seats(up) == 0, seats(up))

up, ong, done = fresh()
r = reserve(up, status='cancelled')
check('client status ignored on create',
      r.status_code == 201 and r.data.get('status') == 'confirmed', r.data)

up, ong, done = fresh()
reserve(up, 2)
reserve(ong, 3)
r = call('GET', '/api/reservations', query={'event_id': up.pk})
check('filter reservations by event_id', len(r.data) == 1, len(r.data))

up, ong, done = fresh()
reserve(up, 2)
c = reserve(up, 2, attendee_email='raj@example.com').data
call('POST', f"/api/reservations/{c['id']}/cancel/", pk=c['id'], action='cancel')
r = call('GET', f'/api/events/{up.pk}/')
check('reservations_count counts confirmed only', r.data['reservations_count'] == 1,
      r.data['reservations_count'])

print('=== cancel ===')
up, ong, done = fresh()
res = reserve(up, 2).data
r = call('POST', 'cancel', pk=res['id'], action='cancel')
check('cancel -> 200', r.status_code == 200, r.data)
check('cancel status', r.data.get('status') == 'cancelled', r.data)
check('cancel restores 498->500', seats(up) == 500, seats(up))

r2 = call('POST', 'cancel', pk=res['id'], action='cancel')
check('cancel twice -> 400', r2.status_code == 400, r2.data)
check('already cancelled message', r2.data.get('error') == 'Already cancelled.', r2.data)
check('no double credit', seats(up) == 500, seats(up))
check('never exceeds total', seats(up) <= up.total_seats, seats(up))

up, ong, done = fresh()
res = reserve(up, 2).data
call('POST', 'cancel', pk=res['id'], action='cancel')
r = call('PATCH', f"/api/reservations/{res['id']}/", body={'seats_reserved': 4})
check('cancelled reservation immutable', r.status_code == 400, r.data)
check('immutable left seats alone', seats(up) == 500, seats(up))

print('=== resize / move ===')
up, ong, done = fresh()
res = reserve(up, 5).data
r = call('PATCH', f"/api/reservations/{res['id']}/", body={'seats_reserved': 2})
check('resize down -> 200', r.status_code == 200, r.data)
check('resize down 495->498', seats(up) == 498, seats(up))

up, ong, done = fresh()
res = reserve(up, 5).data
r = call('PATCH', f"/api/reservations/{res['id']}/", body={'seats_reserved': 600})
check('resize up beyond availability -> 400', r.status_code == 400, r.data)
check('failed resize left seats', seats(up) == 495, seats(up))

up, ong, done = fresh()
res = reserve(up, 10).data
r = call('PATCH', f"/api/reservations/{res['id']}/", body={'event': ong.pk})
check('move to other event -> 200', r.status_code == 200, r.data)
check('source restored', seats(up) == 500, seats(up))
check('target deducted', seats(ong) == 90, seats(ong))

up, ong, done = fresh()
res = reserve(up, 10).data
reserve(ong, 100)
r = call('PATCH', f"/api/reservations/{res['id']}/", body={'event': ong.pk})
check('move into full event -> 400', r.status_code == 400, r.data)
check('failed move source unchanged', seats(up) == 490, seats(up))
check('failed move target unchanged', seats(ong) == 0, seats(ong))

print('=== delete reservation ===')
up, ong, done = fresh()
res = reserve(up, 3).data
check('delete setup 497', seats(up) == 497, seats(up))
r = call('DELETE', f"/api/reservations/{res['id']}/")
check('delete reservation -> 204', r.status_code == 204, r.status_code)
check('row gone', res['id'] not in STORE.reservations)
check('delete restored seats', seats(up) == 500, seats(up))

up, ong, done = fresh()
res = reserve(up, 3).data
call('POST', 'cancel', pk=res['id'], action='cancel')
r = call('DELETE', f"/api/reservations/{res['id']}/")
check('delete cancelled -> 204', r.status_code == 204, r.status_code)
check('delete cancelled did not double credit', seats(up) == 500, seats(up))

up, ong, done = fresh()
r = call('DELETE', '/api/reservations/9999/')
check('delete unknown -> 404', r.status_code == 404, r.status_code)

print('=== middleware ===')
records = []


class Capture(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


M.logger.addHandler(Capture())
M.logger.setLevel(logging.INFO)


class FakeResp:
    status_code = 200


resp = M.RequestLoggingMiddleware(lambda req: FakeResp())(
    Request('GET', '/api/events/'))
check('middleware returns view response', resp.status_code == 200)
check('middleware logged one record', len(records) == 1, records)
if records:
    line = records[0]
    check('log format: method path status duration',
          line.startswith('GET /api/events/ - 200 - ') and line.endswith('s'),
          line)
    print(f'  sample log line: {line}')

print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('FAILURES:', FAIL)
    sys.exit(1)
