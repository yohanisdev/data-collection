import secrets
from datetime import timedelta, timezone
from sqlalchemy import func
from . import db
from .models import Assignment, Contributor, Sentence, Translation, AuditLog, now_utc

def _expire_assignments():
    Assignment.query.filter(Assignment.status == 'assigned', Assignment.expires_at <= now_utc()).update({Assignment.status: 'expired'}, synchronize_session=False)

def _is_expired(value):
    if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
    return value <= now_utc()

def get_next_sentence(contributor_id, ttl_hours=24):
    contributor_query=Contributor.query.filter_by(id=contributor_id)
    if db.engine.dialect.name == 'postgresql': contributor_query=contributor_query.with_for_update()
    if not contributor_query.first():
        db.session.rollback()
        return None
    _expire_assignments()
    current = Assignment.query.filter_by(contributor_id=contributor_id, status='assigned').order_by(Assignment.assigned_at).first()
    if current:
        db.session.commit()
        return current
    submitted = db.session.query(Translation.sentence_id).filter_by(contributor_id=contributor_id).subquery()
    # Randomize the full candidate set; lock each sentence before checking reserved and completed capacity.
    ids = [r[0] for r in db.session.query(Sentence.id).filter(Sentence.is_active.is_(True), ~Sentence.id.in_(submitted)).order_by(func.random()).all()]
    for sentence_id in ids:
        q = Sentence.query.filter_by(id=sentence_id)
        if db.engine.dialect.name == 'postgresql':
            q = q.with_for_update()
        sentence = q.first()
        active = Assignment.query.filter_by(sentence_id=sentence_id, status='assigned').count()
        done = Translation.query.filter_by(sentence_id=sentence_id).count()
        already = Assignment.query.filter_by(contributor_id=contributor_id, sentence_id=sentence_id).filter(Assignment.status.in_(['assigned','submitted'])).first()
        if sentence and active + done < 3 and not already:
            assignment = Assignment(contributor_id=contributor_id, sentence_id=sentence_id, expires_at=now_utc() + timedelta(hours=ttl_hours))
            db.session.add(assignment)
            db.session.commit()
            return assignment
    db.session.commit()
    return None

def submit_translation(contributor_id, assignment_id, text, idempotency_key):
    text = text.strip()
    if not text or len(text) > 10000:
        raise ValueError('Enter a translation of 1 to 10,000 characters.')
    assignment_query = Assignment.query.filter_by(id=assignment_id, contributor_id=contributor_id)
    if db.engine.dialect.name == 'postgresql': assignment_query = assignment_query.with_for_update()
    assignment = assignment_query.first()
    if not assignment:
        raise LookupError('Assignment not found.')
    if assignment.status == 'submitted':
        prior = Translation.query.filter_by(assignment_id=assignment.id).first()
        if prior:
            return prior
        raise ValueError('This assignment has already been submitted.')
    if assignment.status != 'assigned' or _is_expired(assignment.expires_at):
        assignment.status = 'expired'
        db.session.commit()
        raise ValueError('This assignment has expired. Request another sentence.')
    q = Sentence.query.filter_by(id=assignment.sentence_id)
    if db.engine.dialect.name == 'postgresql': q = q.with_for_update()
    sentence = q.first()
    if Translation.query.filter_by(contributor_id=contributor_id, sentence_id=sentence.id).first():
        raise ValueError('You have already submitted a translation for this sentence.')
    if Translation.query.filter_by(sentence_id=sentence.id).count() >= 3:
        raise ValueError('This sentence has received its three translations.')
    prior_key = Assignment.query.filter_by(idempotency_key=idempotency_key).first() if idempotency_key else None
    if prior_key and prior_key.id != assignment.id:
        raise ValueError('This request key has already been used.')
    assignment.status = 'submitted'
    assignment.submitted_at = now_utc()
    assignment.idempotency_key = idempotency_key or secrets.token_urlsafe(18)
    translation = Translation(assignment_id=assignment.id, contributor_id=contributor_id, sentence_id=sentence.id, gedeuffa_text=text)
    db.session.add(translation)
    db.session.commit()
    return translation

def review_translation(translation, admin_id, action, approval_kind=None, reason=None, replace_primary=False):
    translation = Translation.query.filter_by(id=translation.id).with_for_update().first()
    if action == 'approve':
        if approval_kind not in {'primary', 'alternative'}: raise ValueError('Select primary or alternative approval.')
        if approval_kind == 'primary':
            sentence_query = Sentence.query.filter_by(id=translation.sentence_id)
            if db.engine.dialect.name == 'postgresql': sentence_query = sentence_query.with_for_update()
            sentence_query.first()
            current = Translation.query.filter_by(sentence_id=translation.sentence_id, approval_kind='primary').first()
            if current and current.id != translation.id:
                if not replace_primary: raise ValueError('A primary translation already exists. Confirm the explicit replacement to continue.')
                current.approval_kind = 'alternative'
                db.session.add(AuditLog(admin_id=admin_id, translation_id=current.id, action='change_kind_primary_to_alternative', detail=f'Replaced by translation {translation.id}'))
                db.session.flush()
        translation.review_status = 'approved'
        translation.approval_kind = approval_kind
        translation.rejection_reason = None
    else:
        translation.review_status = 'rejected'
        translation.approval_kind = None
        translation.rejection_reason = (reason or '').strip() or None
    translation.reviewed_at = now_utc()
    translation.reviewed_by = admin_id
    db.session.add(AuditLog(admin_id=admin_id, translation_id=translation.id, action=action + (('_' + approval_kind) if approval_kind else ''), detail=translation.rejection_reason))
    db.session.commit()
    return translation
