import secrets
from functools import wraps
from flask import Blueprint, jsonify, request, session, current_app, make_response
from datetime import timedelta, timezone
import hashlib
from . import db, limiter
from .models import Contributor, ContributorSession, Sentence, Assignment, Translation, Admin, now_utc
from .services import get_next_sentence, submit_translation, review_translation

api = Blueprint('api', __name__)

def request_body():
    value=request.get_json(silent=True)
    return value if isinstance(value,dict) else {}

def contributor_id():
    token=request.cookies.get('contributor_session','')
    if not token:return None
    key=hashlib.sha256(token.encode()).hexdigest()
    record=db.session.get(ContributorSession,key)
    if not record or record.ended_at:return None
    expiry=record.expires_at
    if expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
    if expiry<=now_utc():return None
    return record.contributor_id
def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        admin_id = session.get('admin_id')
        if not admin_id or not db.session.get(Admin, admin_id):
            session.clear()
            return jsonify(error='Administrator sign-in required.'), 401
        return fn(*args, **kwargs)
    return wrapped

@api.get('/health')
def health(): return jsonify(status='ok')

@api.get('/sessions/current')
def current_session():
    person = db.session.get(Contributor, contributor_id()) if contributor_id() else None
    return jsonify(active=bool(person), contributor={'name': person.full_name} if person else None)

@api.post('/contributors')
@limiter.limit('8 per hour')
def create_contributor():
    data = request_body()
    if any(not isinstance(data.get(k,''),str) for k in ('full_name','address_or_community','occupation')): return jsonify(error='Profile fields must be text.'),400
    vals = {k: data.get(k, '').strip() for k in ('full_name','address_or_community','occupation')}
    limits = {'full_name':160,'address_or_community':240,'occupation':160}
    if any(not v or len(v)>limits[k] for k,v in vals.items()): return jsonify(error='Please complete each field within the allowed length.'), 400
    if data.get('archive_consent') is not True: return jsonify(error='Please consent to storing your translation in the community archive to continue.'), 400
    if contributor_id() and db.session.get(Contributor, contributor_id()):
        return jsonify(error='A contributor session is already active.'), 409
    person = Contributor(**vals, archive_consent=True, ai_research_consent=data.get('ai_research_consent') is True, consented_at=now_utc())
    db.session.add(person); db.session.flush()
    raw_token=secrets.token_urlsafe(48)
    token_hash=hashlib.sha256(raw_token.encode()).hexdigest()
    db.session.add(ContributorSession(token_hash=token_hash,contributor_id=person.id,expires_at=now_utc()+timedelta(days=current_app.config['CONTRIBUTOR_SESSION_TTL_DAYS'])))
    db.session.commit()
    response=make_response(jsonify(contributor={'name':person.full_name}),201)
    response.set_cookie('contributor_session',raw_token,max_age=current_app.config['CONTRIBUTOR_SESSION_TTL_DAYS']*86400,httponly=True,samesite='Lax',secure=current_app.config['SESSION_COOKIE_SECURE'],path='/')
    return response

@api.post('/assignments/next')
def next_assignment():
    if not contributor_id(): return jsonify(error='Introduce yourself before contributing.'), 401
    assignment = get_next_sentence(contributor_id(), current_app.config['ASSIGNMENT_TTL_HOURS'])
    if not assignment: return jsonify(complete=True, message='Thank you. Every available sentence has been translated.'), 200
    return jsonify(complete=False, assignment_id=assignment.id, sentence={'id':assignment.sentence.id,'english_text':assignment.sentence.english_text})

@api.post('/translations')
def create_translation():
    if not contributor_id(): return jsonify(error='Contributor session required.'), 401
    data = request_body()
    if not isinstance(data.get('assignment_id'), int) or isinstance(data.get('assignment_id'),bool): return jsonify(error='A valid assignment_id is required.'), 400
    if not isinstance(data.get('gedeuffa_text'),str): return jsonify(error='gedeuffa_text must be text.'),400
    try:
        item = submit_translation(contributor_id(), data['assignment_id'], str(data.get('gedeuffa_text') or ''), str(data.get('idempotency_key') or '')[:100])
    except LookupError as e: return jsonify(error=str(e)), 404
    except ValueError as e: db.session.rollback(); return jsonify(error=str(e)), 400
    return jsonify(translation_id=item.id, submitted_at=item.submitted_at.isoformat()), 201

@api.post('/assignments/leave')
def leave():
    if not contributor_id(): return jsonify(error='Contributor session required.'), 401
    data = request_body()
    if not isinstance(data.get('gedeuffa_text',''),str): return jsonify(error='gedeuffa_text must be text.'),400
    assignment_id = data.get('assignment_id')
    if assignment_id is not None and (not isinstance(assignment_id, int) or isinstance(assignment_id, bool)):
        return jsonify(error='A valid assignment_id is required.'), 400
    if assignment_id and data.get('gedeuffa_text','').strip():
        try: submit_translation(contributor_id(), assignment_id, data['gedeuffa_text'], str(data.get('idempotency_key') or '')[:100])
        except (LookupError, ValueError) as e: db.session.rollback(); return jsonify(error=str(e)), 400
    elif assignment_id:
        item = Assignment.query.filter_by(id=assignment_id, contributor_id=contributor_id(), status='assigned').first()
        if item: item.status='cancelled'; db.session.commit()
    token=request.cookies.get('contributor_session','')
    if token:
        record=db.session.get(ContributorSession,hashlib.sha256(token.encode()).hexdigest())
        if record:record.ended_at=now_utc();db.session.commit()
    response=make_response(jsonify(left=True, saved=bool(data.get('gedeuffa_text','').strip())))
    response.delete_cookie('contributor_session',path='/')
    return response

@api.post('/admin/auth/login')
@limiter.limit('10 per minute')
def admin_login():
    data=request_body()
    admin=Admin.query.filter_by(username=str(data.get('username',''))).first()
    if not admin or not admin.verify_password(str(data.get('password',''))): return jsonify(error='Invalid username or password.'), 401
    # Discard any pre-authentication session data before granting privileges.
    session.clear()
    session['admin_id']=admin.id
    session['csrf_token'] = secrets.token_urlsafe(32)
    session.permanent=True
    return jsonify(username=admin.username, csrf_token=session['csrf_token'])

@api.post('/admin/auth/logout')
def admin_logout(): session.pop('admin_id',None); return jsonify(logged_out=True)

@api.get('/admin/overview')
@admin_required
def overview():
    return jsonify(sentences=Sentence.query.count(), active_sentences=Sentence.query.filter_by(is_active=True).count(), pending=Translation.query.filter_by(review_status='pending').count(), approved=Translation.query.filter_by(review_status='approved').count(), translated= db.session.query(Translation.sentence_id).distinct().count())

@api.get('/admin/sentences')
@admin_required
def admin_sentences():
    page=max(1,request.args.get('page',1,type=int)); q=request.args.get('q','').strip()
    query=Sentence.query
    if q: query=query.filter(Sentence.english_text.ilike(f'%{q[:120]}%'))
    status=request.args.get('status','')
    if status in {'pending','approved','rejected'}: query=query.filter(Sentence.id.in_(db.session.query(Translation.sentence_id).filter_by(review_status=status)))
    total=query.count(); rows=query.order_by(Sentence.id).offset((page-1)*20).limit(20).all()
    return jsonify(items=[{'id':s.id,'english_text':s.english_text,'translations':Translation.query.filter_by(sentence_id=s.id).count()} for s in rows],page=page,total=total)

@api.get('/admin/sentences/<int:sentence_id>/translations')
@admin_required
def sentence_translations(sentence_id):
    s=db.session.get(Sentence,sentence_id)
    if not s:return jsonify(error='Sentence not found.'),404
    items=Translation.query.filter_by(sentence_id=s.id).order_by(Translation.submitted_at).all()
    return jsonify(sentence={'id':s.id,'english_text':s.english_text},translations=[{'id':t.id,'text':t.gedeuffa_text,'status':t.review_status,'kind':t.approval_kind,'submitted_at':t.submitted_at.isoformat(),'reviewed_at':t.reviewed_at.isoformat() if t.reviewed_at else None,'reason':t.rejection_reason} for t in items])

@api.post('/admin/translations/<int:translation_id>/approve')
@admin_required
def approve(translation_id):
    item=db.session.get(Translation,translation_id)
    if not item:return jsonify(error='Translation not found.'),404
    data=request_body()
    try: item=review_translation(item,session['admin_id'],'approve',data.get('kind'),replace_primary=data.get('replace_primary') is True)
    except ValueError as e: db.session.rollback(); return jsonify(error=str(e)),409
    return jsonify(id=item.id,status=item.review_status,kind=item.approval_kind)

@api.post('/admin/translations/<int:translation_id>/reject')
@admin_required
def reject(translation_id):
    item=db.session.get(Translation,translation_id)
    if not item:return jsonify(error='Translation not found.'),404
    data=request_body()
    if not isinstance(data.get('reason',''),str):return jsonify(error='reason must be text.'),400
    item=review_translation(item,session['admin_id'],'reject',reason=data.get('reason'))
    return jsonify(id=item.id,status=item.review_status)

@api.get('/admin/dataset/export')
@admin_required
def export_dataset():
    from flask import Response
    import csv, io
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(['english_text','gedeuffa_text','approval_kind'])
    rows=db.session.query(Sentence.english_text,Translation.gedeuffa_text,Translation.approval_kind).join(Translation,Translation.sentence_id==Sentence.id).join(Contributor,Translation.contributor_id==Contributor.id).filter(Translation.review_status=='approved',Contributor.ai_research_consent.is_(True)).order_by(Translation.sentence_id,Translation.approval_kind).all()
    def spreadsheet_safe(value):
        value = value or ''
        first_visible = value.lstrip(' \t\r\n\v\f')[:1]
        return "'" + value if first_visible in ('=', '+', '-', '@') else value
    for english_text, gedeuffa_text, approval_kind in rows:
        writer.writerow([spreadsheet_safe(english_text),spreadsheet_safe(gedeuffa_text),approval_kind])
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition':'attachment; filename=approved-translations.csv'})
