import csv, hashlib, os
from . import db
from .models import Sentence

def import_csv(path, column='english_text', dry_run=False):
    stats = {'imported': 0, 'skipped': 0, 'invalid': 0, 'duplicates': 0}
    # The Flask config is attached by the CLI and tests via current_app; importing here avoids app setup cycles.
    from flask import current_app
    if os.path.getsize(path) > current_app.config['MAX_CSV_BYTES']:
        raise ValueError('CSV file exceeds the configured size limit.')
    with open(path, 'r', encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or column not in reader.fieldnames:
            raise ValueError(f"CSV must include required column '{column}'. Found: {reader.fieldnames}")
        seen = set()
        for row_number, row in enumerate(reader, start=1):
            if row_number > current_app.config['MAX_CSV_ROWS']:
                raise ValueError('CSV file exceeds the configured row limit.')
            if None in row:
                stats['invalid'] += 1
                continue
            text = row.get(column)
            if text is None:
                stats['invalid'] += 1; continue
            if not text.strip():
                stats['skipped'] += 1; continue
            key = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if key in seen or Sentence.query.filter_by(source_key=key).first():
                stats['duplicates'] += 1; continue
            seen.add(key)
            if not dry_run:
                db.session.add(Sentence(source_key=key, english_text=text))
            stats['imported'] += 1
    if not dry_run: db.session.commit()
    return stats
