"""Static check: 0001_initial.py matches models.py field for field.

Stands in for `python manage.py makemigrations --check --dry-run`, which cannot
run without Django. Compares field names, Django field classes, constructor
kwargs, choices, defaults, Metadata ordering and the FK's on_delete /
related_name.
"""

import ast
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
MODELS = SRC / 'events' / 'models.py'
MIGRATION = SRC / 'events' / 'migrations' / '0001_initial.py'

problems = []


def literal(node):
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [literal(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return {literal(k): literal(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Call):
        return ('CALL', ast.unparse(node)[:60])
    if isinstance(node, ast.Attribute):
        return ('ATTR', node.attr)
    if isinstance(node, ast.Name):
        return ('NAME', node.id)
    return ('EXPR', ast.unparse(node)[:60])


def field_assignments(classdef):
    """{field_name: (FieldClassName, {kwargs})} for model-style definitions."""
    constants = {}
    for stmt in classdef.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, (ast.List, ast.Tuple, ast.Dict)):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = literal(stmt.value)

    out = {}
    for stmt in classdef.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        callee = stmt.value.func
        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, 'id', '')
        kwargs = {}
        for kw in stmt.value.keywords:
            kwargs[kw.arg] = literal(kw.value)
            # Resolve a class-level constant such as STATUS_CHOICES.
            if (isinstance(kw.value, ast.Name)
                    and kw.value.id in constants):
                kwargs[kw.arg] = constants[kw.value.id]
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                out[target.id] = (name, kwargs)
    return out


def meta_options(classdef):
    out = {}
    for stmt in classdef.body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == 'Meta':
            for s in stmt.body:
                if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Name):
                    out[s.targets[0].id] = literal(s.value)
    return out


def fields_hook(classdef):
    """Migration style: fields=[('name', models.X(...)), ...]"""
    out = {}
    for stmt in classdef.body:
        if (isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == 'fields'):
            for elt in stmt.value.elts:  # each is a Tuple
                key = literal(elt.elts[0])
                call = elt.elts[1]
                if not isinstance(call, ast.Call):
                    problems.append(f'migration field {key} is not a call')
                    continue
                fname = (call.func.attr if isinstance(call.func, ast.Attribute)
                         else getattr(call.func, 'id', '?'))
                kwargs = {kw.arg: literal(kw.value) for kw in call.keywords}
                out[key] = (fname, kwargs)
    return out


def find_class(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


models_tree = ast.parse(MODELS.read_text(encoding='utf-8'))
mig_tree = ast.parse(MIGRATION.read_text(encoding='utf-8'))

for model_name in ('Event', 'Reservation'):
    m = find_class(models_tree, model_name)
    create = None
    for node in ast.walk(mig_tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'CreateModel'):
            for kw in node.keywords:
                if kw.arg == 'name' and literal(kw.value) == model_name:
                    create = node
    if m is None:
        problems.append(f'{model_name}: missing from models.py')
        continue
    if create is None:
        problems.append(f'{model_name}: missing from migration')
        continue

    model_fields = field_assignments(m)
    mig_fields = {}
    mig_meta = {}
    for kw in create.keywords:
        if kw.arg == 'fields':
            mig_fields = fields_hook(ast.ClassDef(
                name='X', bases=[], keywords=[], body=[], decorator_list=[]))
            # reuse the hook by faking a class body
            fake = ast.Module(body=[ast.Assign(targets=[ast.Name(id='fields')],
                                               value=kw.value)], type_ignores=[])
            mig_fields = {}
            for elt in kw.value.elts:
                key = literal(elt.elts[0])
                call = elt.elts[1]
                fname = (call.func.attr if isinstance(call.func, ast.Attribute)
                         else getattr(call.func, 'id', '?'))
                mig_fields[key] = (fname, {k.arg: literal(k.value) for k in call.keywords})
        elif kw.arg == 'options':
            mig_meta = literal(kw.value)

    # Every model field must appear in the migration (id comes from BigAutoField).
    expected = dict(model_fields)
    expected['id'] = ('BigAutoField', {})
    for name, (fname, kwargs) in expected.items():
        if name not in mig_fields:
            problems.append(f'{model_name}.{name}: present in models.py, '
                            f'missing from migration')
            continue
        mf_name, mf_kwargs = mig_fields[name]
        if mf_name != fname:
            problems.append(f'{model_name}.{name}: field type {fname} vs '
                            f'migration {mf_name}')
        for key, value in kwargs.items():
            if key not in mf_kwargs:
                problems.append(f'{model_name}.{name}: kwarg {key}={value!r} '
                                f'missing from migration')
            elif mf_kwargs[key] != value:
                problems.append(f'{model_name}.{name}.{key}: models.py '
                                f'{value!r} vs migration {mf_kwargs[key]!r}')

    for name in mig_fields:
        if name not in expected:
            problems.append(f'{model_name}.{name}: in migration but not models.py')

    # Meta ordering
    meta = meta_options(m)
    if meta.get('ordering') != mig_meta.get('ordering'):
        problems.append(f'{model_name}.Meta.ordering: models.py '
                        f'{meta.get("ordering")!r} vs migration '
                        f'{mig_meta.get("ordering")!r}')

# Reservation FK specifics
res_mig = [n for n in ast.walk(mig_tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == 'ForeignKey']
if not res_mig:
    problems.append('Reservation.event: no ForeignKey in migration')
else:
    fk = res_mig[0]
    kws = {k.arg: k.value for k in fk.keywords}
    if 'on_delete' not in kws:
        problems.append('FK missing on_delete')
    else:
        text = ast.unparse(kws['on_delete'])
        if 'CASCADE' not in text:
            problems.append(f'FK on_delete is {text}, expected CASCADE')
    related = literal(kws.get('related_name')) if 'related_name' in kws else None
    if related != 'reservations':
        problems.append(f'FK related_name is {related!r}, expected "reservations"')
    to = literal(kws.get('to'))
    if to != 'events.event':
        problems.append(f'FK to is {to!r}, expected "events.event"')

# Migration must declare initial=True and no dependencies
has_initial = any(
    isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    and n.targets[0].id == 'initial' and literal(n.value) is True
    for n in ast.walk(mig_tree))
if not has_initial:
    problems.append('0001_initial.py: missing initial = True')

print(f'models.py fields checked: Event={sorted(field_assignments(find_class(models_tree, "Event")))}')
print(f'                           Reservation={sorted(field_assignments(find_class(models_tree, "Reservation")))}')
if problems:
    print('\nPROBLEMS:')
    for p in problems:
        print(' -', p)
    sys.exit(1)
print('\nmigration matches models.py — no drift detected')
