"""PostgreSQL-only concurrency tests for an explicitly confirmed disposable database.

Set TEST_DATABASE_URL to a database whose name ends in ``_test`` and set
TEST_DATABASE_CONFIRM=I_UNDERSTAND_THIS_DELETES_TEST_DATA. These tests create
and drop every application table in that database; they refuse other databases.
"""
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import text

from backend import create_app, db
from backend.models import Assignment, Contributor, Sentence, Translation, now_utc
from backend.services import get_next_sentence, submit_translation

DATABASE = os.getenv('TEST_DATABASE_URL')
CONFIRMATION = 'I_UNDERSTAND_THIS_DELETES_TEST_DATA'
pytestmark = pytest.mark.skipif(
    not DATABASE or not DATABASE.startswith('postgresql'),
    reason='requires disposable PostgreSQL TEST_DATABASE_URL',
)


@pytest.fixture(scope='module')
def app():
    database_name = unquote(urlparse(DATABASE).path.lstrip('/'))
    if not database_name.endswith('_test'):
        pytest.fail('TEST_DATABASE_URL must name a dedicated database ending in _test.')
    if os.getenv('TEST_DATABASE_CONFIRM') != CONFIRMATION:
        pytest.fail('Set TEST_DATABASE_CONFIRM to the documented confirmation before destructive PostgreSQL tests.')
    application = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret'})
    with application.app_context():
        actual_name = db.session.execute(text('SELECT current_database()')).scalar_one()
        assert actual_name == database_name
        db.drop_all()
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def seed(app, people=20):
    with app.app_context():
        source = f'Concurrent sentence {people}.'
        sentence = Sentence(source_key=hashlib.sha256(source.encode()).hexdigest(), english_text=source)
        contributors = [Contributor(full_name=f'Person {i}', address_or_community='Town', occupation='Speaker', archive_consent=True) for i in range(people)]
        db.session.add_all([sentence, *contributors])
        db.session.commit()
        return sentence.id, [person.id for person in contributors]


def simultaneous(items, action):
    barrier = Barrier(len(items))

    def run(item):
        barrier.wait(timeout=10)
        return action(item)

    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(run, items))


def test_simultaneous_reservations_and_retries_do_not_exceed_capacity(app):
    sentence_id, contributor_ids = seed(app)

    def reserve(contributor_id):
        with app.app_context():
            assignment = get_next_sentence(contributor_id)
            return assignment.id if assignment else None

    reservations = simultaneous(contributor_ids, reserve)
    assigned = [(contributor_id, assignment_id) for contributor_id, assignment_id in zip(contributor_ids, reservations) if assignment_id]
    assert len(assigned) == 3
    assert len({assignment_id for _, assignment_id in assigned}) == 3

    requests = [pair for pair in assigned for _ in range(2)]

    def submit(pair):
        contributor_id, assignment_id = pair
        with app.app_context():
            return submit_translation(contributor_id, assignment_id, 'Gede’uffa translation', f'concurrent-{contributor_id}').id

    results = simultaneous(requests, submit)
    assert len(set(results)) == 3
    with app.app_context():
        translations = Translation.query.filter_by(sentence_id=sentence_id).all()
        assert len(translations) == 3
        assert len({translation.contributor_id for translation in translations}) == 3
        assert Assignment.query.filter_by(sentence_id=sentence_id, status='assigned').count() == 0


def test_competing_submission_cannot_complete_a_fourth_translation(app):
    sentence_id, contributor_ids = seed(app, people=4)
    with app.app_context():
        assignments = [Assignment(contributor_id=contributor_id, sentence_id=sentence_id, expires_at=now_utc() + timedelta(hours=1)) for contributor_id in contributor_ids]
        db.session.add_all(assignments)
        db.session.commit()
        pairs = [(assignment.contributor_id, assignment.id) for assignment in assignments]

    def submit(pair):
        with app.app_context():
            try:
                return ('ok', submit_translation(pair[0], pair[1], 'Translation', f'capacity-{pair[0]}').id)
            except ValueError as error:
                db.session.rollback()
                return ('rejected', str(error))

    outcomes = simultaneous(pairs, submit)
    assert [status for status, _ in outcomes].count('ok') == 3
    assert [status for status, _ in outcomes].count('rejected') == 1
    with app.app_context():
        assert Translation.query.filter_by(sentence_id=sentence_id).count() == 3


def test_postgresql_ownership_expiration_and_rollback(app):
    sentence_id, contributor_ids = seed(app, people=2)
    with app.app_context():
        first = Assignment(contributor_id=contributor_ids[0], sentence_id=sentence_id, expires_at=now_utc() + timedelta(hours=1))
        second = Assignment(contributor_id=contributor_ids[1], sentence_id=sentence_id, expires_at=now_utc() + timedelta(hours=1))
        db.session.add_all([first, second])
        db.session.commit()
        first_id, second_id = first.id, second.id
        with pytest.raises(LookupError):
            submit_translation(contributor_ids[1], first_id, 'Wrong owner', 'wrong-owner')
        db.session.rollback()
        submit_translation(contributor_ids[0], first_id, 'First', 'reused-key')
        with pytest.raises(ValueError, match='already been used'):
            submit_translation(contributor_ids[1], second_id, 'Second', 'reused-key')
        db.session.rollback()
        assert db.session.get(Assignment, second_id).status == 'assigned'
        expired = db.session.get(Assignment, second_id)
        expired.expires_at = now_utc() - timedelta(seconds=1)
        db.session.commit()
        with pytest.raises(ValueError, match='expired'):
            submit_translation(contributor_ids[1], second_id, 'Late', 'new-key')
        db.session.rollback()
        assert db.session.get(Assignment, second_id).status == 'expired'
