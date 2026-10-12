#!/usr/bin/env python3
"""Local server for the travel map.

Serves this folder at http://localhost:8765 and lets the page add, edit and
delete rows in places.csv. Run with:  python3 server.py
"""
import csv
import functools
import io
import json
import os
import re
import shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(ROOT, 'places.csv')
BACKUP_PATH = os.path.join(ROOT, 'places.backup.csv')
SAMPLE_PATH = os.path.join(ROOT, 'places.sample.csv')
PORT = 8765
ALLOWED_HOSTS = {f'localhost:{PORT}', f'127.0.0.1:{PORT}'}
TYPES = ('city', 'country', 'National Park', 'home')

# Form field -> header names it may be stored under (compared lowercase, letters only).
# The first name is used when the column has to be created.
COLUMNS = {
    'type': ['type', 'kind', 'category'],
    'name': ['name', 'place', 'location', 'title'],
    'state': ['state', 'province', 'region', 'stateprovince'],
    'country': ['country'],
    'date': ['date_visited', 'date', 'visited', 'when'],
    'notes': ['notes', 'note', 'description', 'comments', 'comment'],
    'status': ['status', 'wishlist'],
    'trip': ['trip', 'tripname'],
    'photo': ['photo', 'image', 'picture', 'photourl'],
    'link': ['link', 'url', 'website'],
}
# Coordinates are cleared when a place is renamed so the map looks it up again.
COORDS = ['lat', 'latitude', 'lng', 'lon', 'long', 'longitude']


class Rejected(Exception):
    pass


def norm(value):
    return re.sub(r'[^a-z0-9]', '', value.lower())


def read_table():
    """Returns (header, data rows, line ending), skipping blank lines like the page does."""
    with open(CSV_PATH, 'r', encoding='utf-8-sig', newline='') as f:
        text = f.read()
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise Rejected('places.csv has no header row')
    return rows[0], rows[1:], '\r\n' if '\r\n' in text else '\n'


def write_table(header, rows, eol):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator=eol)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row + [''] * (len(header) - len(row)))
    tmp = CSV_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(out.getvalue())
    os.replace(tmp, CSV_PATH)


def column(header, field, create=False):
    keys = [norm(h) for h in header]
    names = [norm(n) for n in COLUMNS[field]]
    index = next((i for i, k in enumerate(keys) if k in names), None)
    if index is None and create:
        header.append(COLUMNS[field][0])
        index = len(header) - 1
    return index


def fill(header, row, place):
    for field in COLUMNS:
        if field not in place:
            continue
        index = column(header, field, create=bool(place[field]))
        if index is not None:
            row.extend([''] * (len(header) - len(row)))
            row[index] = place[field]


def identity(header, row):
    cells = (column(header, f) for f in ('name', 'state', 'country'))
    return [row[i] if i is not None and i < len(row) else '' for i in cells]


def find_row(header, rows, number, expect):
    """Row numbers come from the page; the name check guards against a file edited in between."""
    name_col = column(header, 'name')
    if type(number) is not int or not 1 <= number <= len(rows) or name_col is None:
        raise Rejected('That place is no longer in places.csv. Reload the map.')
    row = rows[number - 1]
    if (row[name_col].strip() if name_col < len(row) else '') != expect:
        raise Rejected('places.csv changed since the map loaded. Reload the map and try again.')
    return row


def add_place(place):
    header, rows, eol = read_table()
    row = []
    fill(header, row, place)
    rows.append(row)
    write_table(header, rows, eol)


def update_place(number, expect, place):
    header, rows, eol = read_table()
    row = find_row(header, rows, number, expect)
    before = identity(header, row)
    shutil.copyfile(CSV_PATH, BACKUP_PATH)
    fill(header, row, place)
    if identity(header, row) != before:
        for i, key in enumerate(norm(h) for h in header):
            if key in COORDS and i < len(row):
                row[i] = ''
    write_table(header, rows, eol)


def delete_place(number, expect):
    header, rows, eol = read_table()
    row = find_row(header, rows, number, expect)
    shutil.copyfile(CSV_PATH, BACKUP_PATH)
    del rows[number - 1]
    write_table(header, rows, eol)


def set_trip(name, places):
    """Puts the same trip name on several rows at once (rename or combine trips)."""
    name = ' '.join(str(name).split())
    if not name or len(name) > 80:
        raise Rejected('Enter a trip name (up to 80 characters)')
    if not isinstance(places, list) or not places or len(places) > 500:
        raise Rejected('No places selected')
    header, rows, eol = read_table()
    targets = [find_row(header, rows, p.get('row') if isinstance(p, dict) else None,
                        str(p.get('expect', '')) if isinstance(p, dict) else '') for p in places]
    shutil.copyfile(CSV_PATH, BACKUP_PATH)
    for row in targets:
        fill(header, row, {'trip': name})
    write_table(header, rows, eol)


def clean(body):
    place = {k: ' '.join(str(body[k]).split()) for k in COLUMNS if k in body}
    if not place.get('name'):
        raise Rejected('Name is required')
    if place.get('type') not in TYPES:
        raise Rejected('Unknown type of place')
    if place.get('status', '') not in ('', 'wishlist'):
        raise Rejected('Bad status')
    # Links must be web addresses; photos may also be a file in this folder (photos/lisbon.jpg).
    for field in ('photo', 'link'):
        value = place.get(field, '')
        local = field == 'photo' and ':' not in value and not value.startswith('/')
        if value and not re.match(r'^https?://', value) and not local:
            raise Rejected('Photos and links must start with http:// or https://')
    return place


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def reply(self, status, body):
        data = json.dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        actions = {'/api/places': 'add', '/api/places/update': 'update', '/api/places/delete': 'delete',
                   '/api/trips': 'trip'}
        action = actions.get(self.path)
        if not action:
            return self.reply(404, {'error': 'Not found'})
        # Only the map page itself may write: other sites can't send JSON here without a preflight.
        if (self.headers.get('Host') not in ALLOWED_HOSTS
                or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
            return self.reply(403, {'error': 'Forbidden'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(min(length, 20000)))
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.reply(400, {'error': 'Bad request'})
        try:
            if action == 'add':
                add_place(clean(body))
            elif action == 'update':
                place = body.get('place')
                update_place(body.get('row'), str(body.get('expect', '')), clean(place if isinstance(place, dict) else {}))
            elif action == 'trip':
                set_trip(body.get('trip', ''), body.get('places'))
            else:
                delete_place(body.get('row'), str(body.get('expect', '')))
        except Rejected as e:
            return self.reply(400, {'error': str(e)})
        except PermissionError:
            return self.reply(500, {'error': "Couldn't write places.csv. Close it in Excel and try again."})
        except OSError as e:
            return self.reply(500, {'error': str(e)})
        self.reply(200, {'ok': True})


if __name__ == '__main__':
    # A fresh copy of the project starts from the sample places.
    if not os.path.exists(CSV_PATH) and os.path.exists(SAMPLE_PATH):
        shutil.copyfile(SAMPLE_PATH, CSV_PATH)
    handler = functools.partial(Handler, directory=ROOT)
    print(f'Travel map running at http://localhost:{PORT}  (Ctrl+C to stop)')
    ThreadingHTTPServer(('127.0.0.1', PORT), handler).serve_forever()
