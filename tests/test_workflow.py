import hashlib
import os
from datetime import timedelta
import pytest
from backend import db
from backend.models import Contributor, Sentence, Assignment, Translation, Admin, AuditLog, now_utc
from backend.services import get_next_sentence, submit_translation

def person(n): return Contributor(full_name=f'Person {n}',address_or_community='Community',occupation='Teacher')
def sentence(text='A sentence.'): return Sentence(source_key=hashlib.sha256(text.encode()).hexdigest(),english_text=text)

def test_three_assignments_then_closed(app):
    with app.app_context():
        s=sentence(); people=[person(n) for n in range(4)]; db.session.add_all([s,*people]);db.session.commit()
        assignments=[get_next_sentence(p.id) for p in people[:3]]
        assert all(a and a.sentence_id==s.id for a in assignments)
        assert get_next_sentence(people[3].id) is None
        for i,a in enumerate(assignments): submit_translation(people[i].id,a.id,f'Gede translation {i}',f'key-{i}')
        assert Translation.query.filter_by(sentence_id=s.id).count()==3
        assert get_next_sentence(people[3].id) is None

def test_contributor_cannot_submit_another_assignment(app):
    with app.app_context():
        s=sentence(); a,b=person(1),person(2);db.session.add_all([s,a,b]);db.session.commit()
        assignment=get_next_sentence(a.id)
        try: submit_translation(b.id,assignment.id,'translation','other')
        except LookupError: pass
        else: assert False, 'assignment ownership must be checked'

def test_retries_do_not_duplicate_translation(app):
    with app.app_context():
        s=sentence();p=person(1);db.session.add_all([s,p]);db.session.commit();a=get_next_sentence(p.id)
        first=submit_translation(p.id,a.id,'  ገደኡፋ  ','same-key')
        second=submit_translation(p.id,a.id,'a changed retry','same-key')
        assert first.id==second.id
        assert Translation.query.filter_by(sentence_id=s.id).count()==1
        assert first.gedeuffa_text=='ገደኡፋ'

def test_expired_reservation_is_released(app):
    with app.app_context():
        s=sentence();a,b,c,d=[person(n) for n in range(4)];db.session.add_all([s,a,b,c,d]);db.session.commit()
        first=get_next_sentence(a.id);get_next_sentence(b.id);get_next_sentence(c.id)
        first.expires_at=now_utc()-timedelta(hours=1);db.session.commit()
        assert get_next_sentence(d.id) is not None
        db.session.refresh(first);assert first.status=='expired'

def test_api_and_admin_approval_rules(client,app):
    with app.app_context():
        s=sentence();p1,p2=person(1),person(2);admin=Admin(username='editor',password_hash=Admin.hash_password('long-enough-password'))
        db.session.add_all([s,p1,p2,admin]);db.session.commit(); admin_id=admin.id
        a1=get_next_sentence(p1.id);a2=get_next_sentence(p2.id)
        submit_translation(p1.id,a1.id,'First translation','k1');submit_translation(p2.id,a2.id,'Second translation','k2')
        ids=[t.id for t in Translation.query.order_by(Translation.id).all()]
    assert client.get('/api/admin/overview').status_code==401
    # Simulate established admin session and a CSRF token obtained by the client.
    token=client.get('/api/csrf').json['csrf_token']
    with client.session_transaction() as sess: sess['admin_id']=admin_id
    headers={'X-CSRF-Token':token}
    r=client.post(f'/api/admin/translations/{ids[0]}/approve',json={'kind':'primary'},headers=headers);assert r.status_code==200
    r=client.post(f'/api/admin/translations/{ids[1]}/approve',json={'kind':'primary'},headers=headers);assert r.status_code==409
    r=client.post(f'/api/admin/translations/{ids[1]}/approve',json={'kind':'primary','replace_primary':True},headers=headers);assert r.status_code==200
    with app.app_context():
        assert AuditLog.query.count()==3
        assert Translation.query.get(ids[0]).approval_kind=='alternative'
        assert Translation.query.get(ids[1]).approval_kind=='primary'

def test_contributor_consent_and_opaque_session_cookie(client,app):
    csrf=client.get('/api/csrf').json['csrf_token'];headers={'X-CSRF-Token':csrf}
    payload={'full_name':'Speaker','address_or_community':'Community','occupation':'Farmer'}
    assert client.post('/api/contributors',json=payload,headers=headers).status_code==400
    payload.update(archive_consent=True,ai_research_consent=False)
    response=client.post('/api/contributors',json=payload,headers=headers)
    assert response.status_code==201
    cookie=';'.join(response.headers.getlist('Set-Cookie'))
    assert 'contributor_session=' in cookie and 'HttpOnly' in cookie
    assert client.get('/api/sessions/current').json['active'] is True
    with app.app_context():
        p=Contributor.query.first()
        assert p.archive_consent and not p.ai_research_consent and p.consented_at

def test_request_hardening_and_invalid_leave_input(client):
    csrf=client.get('/api/csrf').json['csrf_token']; headers={'X-CSRF-Token':csrf}
    response=client.get('/api/health')
    assert response.headers['X-Content-Type-Options']=='nosniff'
    assert response.headers['X-Frame-Options']=='DENY'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    assert client.post('/api/assignments/leave',json={'assignment_id':'not-an-id'},headers=headers).status_code==401
    profile={'full_name':'Speaker','address_or_community':'Community','occupation':'Farmer','archive_consent':True}
    assert client.post('/api/contributors',json=profile,headers=headers).status_code==201
    assert client.post('/api/assignments/leave',json={'assignment_id':'not-an-id'},headers=headers).status_code==400
    response=client.post('/api/translations',json={'assignment_id':1,'gedeuffa_text':'x' * (33 * 1024)},headers=headers)
    assert response.status_code==413 and response.json['error'].startswith('Request body exceeds')

def test_admin_login_rotates_pre_auth_session_and_csv_is_spreadsheet_safe(client,app):
    with app.app_context():
        s=sentence(' \t=cmd()'); p=person(1); p.ai_research_consent=True
        admin=Admin(username='editor',password_hash=Admin.hash_password('long-enough-password'))
        db.session.add_all([s,p,admin]); db.session.commit()
        assignment=Assignment(contributor_id=p.id,sentence_id=s.id,status='submitted',expires_at=now_utc())
        db.session.add(assignment); db.session.flush()
        db.session.add(Translation(assignment_id=assignment.id,contributor_id=p.id,sentence_id=s.id,gedeuffa_text='\r+danger ገደኡፋ',review_status='approved',approval_kind='primary'))
        db.session.commit()
    csrf=client.get('/api/csrf').json['csrf_token']
    with client.session_transaction() as sess: sess['csrf_token']=csrf; sess['untrusted_pre_auth_value']='discard-me'
    response=client.post('/api/admin/auth/login',json={'username':'editor','password':'long-enough-password'},headers={'X-CSRF-Token':csrf})
    assert response.status_code==200 and response.json['csrf_token'] != csrf
    with client.session_transaction() as sess:
        assert 'untrusted_pre_auth_value' not in sess and sess['admin_id']
    export=client.get('/api/admin/dataset/export')
    assert export.status_code==200
    assert "' \t=cmd()" in export.text and "'\r+danger ገደኡፋ" in export.text

def test_production_startup_rejects_weak_secrets_and_non_redis_rate_limits(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('COOKIE_SECURE', '1')
    monkeypatch.setenv('ALLOWED_HOSTS', 'example.test')
    monkeypatch.setenv('SECRET_KEY', '7w2Vn9!pQ4#zK8@cR6$mT1^xB3&fH5*j')
    monkeypatch.setenv('EDGE_PROXY_SECRET', '9pV5!cR2@hW7#xM4^tK8&bQ1*zF6$yN3')
    monkeypatch.delenv('RATELIMIT_STORAGE_URI', raising=False)
    from backend import create_app
    with pytest.raises(RuntimeError, match='TLS-protected Redis'):
        create_app()
    monkeypatch.setenv('RATELIMIT_STORAGE_URI', 'rediss://default:secret@redis.example:6379/0')
    monkeypatch.setenv('SECRET_KEY', 'too-short')
    with pytest.raises(RuntimeError, match='at least 32 characters'):
        create_app()

