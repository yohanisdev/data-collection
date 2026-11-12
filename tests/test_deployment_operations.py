import csv
import hashlib
import os
import pytest
from flask_migrate import upgrade
from backend import create_app, db
from backend.models import Admin, Sentence, Contributor, Assignment, Translation, AuditLog, ContributorSession, ProvisioningMarker
from backend.importer import import_csv

def test_migration_execution_and_repeatability(tmp_path):
    """Verify migrations run successfully, create all tables, and repeated runs are safe and idempotent."""
    db_file = tmp_path / 'migrate_test.db'
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'test-secret-key-32-chars-long!',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{db_file}'
    })
    with app.app_context():
        # First execution: runs Alembic migrations from scratch
        upgrade(directory=os.path.join(app.root_path, 'migrations'))
        
        # Verify all tables exist and can be queried
        inspector = db.inspect(db.engine)
        tables = set(inspector.get_table_names())
        expected_tables = {
            'alembic_version', 'contributor', 'admin', 'contributor_session',
            'sentence', 'assignment', 'translation', 'audit_log', 'provisioning_marker'
        }
        assert expected_tables.issubset(tables)

        # Seed some data to ensure subsequent migration execution does not destroy data
        admin = Admin(username='existing_admin', password_hash=Admin.hash_password('valid-admin-password'))
        sentence = Sentence(source_key='test-key-01', english_text='Sample sentence')
        db.session.add_all([admin, sentence])
        db.session.commit()

        # Second execution: repeated migration run must be idempotent and preserve existing data
        upgrade(directory=os.path.join(app.root_path, 'migrations'))
        assert Admin.query.filter_by(username='existing_admin').first() is not None
        assert Sentence.query.filter_by(source_key='test-key-01').first() is not None

def test_csv_import_deduplication(app, tmp_path):
    """Verify SHA-256 deduplication within CSV and against database on rerun."""
    csv_file = tmp_path / 'sentences.csv'
    with csv_file.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['english'])
        w.writeheader()
        w.writerow({'english': 'Sentence Alpha'})
        w.writerow({'english': 'Sentence Alpha'})  # In-file duplicate
        w.writerow({'english': 'Sentence Beta'})
        w.writerow({'english': ''})               # Skipped empty
        w.writerow({'english': '   '})            # Skipped whitespace

    with app.app_context():
        stats = import_csv(str(csv_file), column='english', batch_size=2)
        assert stats == {'imported': 2, 'skipped': 2, 'invalid': 0, 'duplicates': 1}
        assert Sentence.query.count() == 2

        # Rerun on same file: must not duplicate sentences
        rerun_stats = import_csv(str(csv_file), column='english', batch_size=2)
        assert rerun_stats == {'imported': 0, 'skipped': 2, 'invalid': 0, 'duplicates': 3}
        assert Sentence.query.count() == 2

def test_invalid_csv_input(app, tmp_path):
    """Verify validation of file size, row limits, missing column, and malformed rows."""
    # 1. Missing required column
    bad_col_file = tmp_path / 'bad_col.csv'
    bad_col_file.write_text("id,french\n1,bonjour\n", encoding='utf-8')
    with app.app_context():
        with pytest.raises(ValueError, match="CSV must include required column 'english'"):
            import_csv(str(bad_col_file), column='english')

    # 2. File size limit exceeded
    large_file = tmp_path / 'large.csv'
    large_file.write_text("english\n" + ("a" * 1024 * 1024 * 11) + "\n", encoding='utf-8')
    with app.app_context():
        with pytest.raises(ValueError, match="CSV file exceeds the configured size limit"):
            import_csv(str(large_file), column='english')

    # 3. Row limit exceeded
    row_limit_file = tmp_path / 'row_limit.csv'
    with row_limit_file.open('w', encoding='utf-8') as f:
        f.write("english\n")
        for i in range(15):
            f.write(f"Sentence {i}\n")
    app.config['MAX_CSV_ROWS'] = 10
    with app.app_context():
        with pytest.raises(ValueError, match="CSV file exceeds the configured row limit"):
            import_csv(str(row_limit_file), column='english')
    app.config['MAX_CSV_ROWS'] = 100000

    # 4. Malformed row / None values
    malformed_file = tmp_path / 'malformed.csv'
    malformed_file.write_text("english\nValid row\nExtra,column,row\n", encoding='utf-8')
    with app.app_context():
        stats = import_csv(str(malformed_file), column='english')
        assert stats['imported'] == 1
        assert stats['invalid'] == 1

def test_partial_import_failure_and_recovery(app, tmp_path, monkeypatch):
    """Verify rollback on batch failure and safe recovery on subsequent retry."""
    csv_file = tmp_path / 'batch_test.csv'
    with csv_file.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['english'])
        w.writeheader()
        for i in range(10):
            w.writerow({'english': f'Batch Sentence {i}'})

    with app.app_context():
        # Dry run does not commit anything
        dry_stats = import_csv(str(csv_file), column='english', dry_run=True, batch_size=5)
        assert dry_stats['imported'] == 10
        assert Sentence.query.count() == 0

        # Simulate failure during batch commit by patching commit on batch 2
        real_commit = db.session.commit
        commit_calls = {'count': 0}
        def failing_commit():
            commit_calls['count'] += 1
            if commit_calls['count'] == 2:
                raise RuntimeError("Simulated database failure during batch 2")
            real_commit()

        monkeypatch.setattr(db.session, 'commit', failing_commit)

        with pytest.raises(RuntimeError, match="Simulated database failure"):
            import_csv(str(csv_file), column='english', batch_size=5)

        # Batch 1 was committed before failure; batch 2 was rolled back
        assert Sentence.query.count() == 5

        # Restore normal commit and rerun
        monkeypatch.setattr(db.session, 'commit', real_commit)
        recovery_stats = import_csv(str(csv_file), column='english', batch_size=5)
        assert recovery_stats['imported'] == 5
        assert recovery_stats['duplicates'] == 5
        assert Sentence.query.count() == 10

def test_initial_admin_provisioning_success(client, app):
    """Verify secure initial administrator provisioning with valid token, CSRF, and proxy headers."""
    setup_token = "secure-setup-token-at-least-32-chars-long!"
    app.config['INITIAL_ADMIN_TOKEN'] = setup_token

    csrf_resp = client.get('/api/csrf')
    csrf_token = csrf_resp.json['csrf_token']

    headers = {
        'X-CSRF-Token': csrf_token,
        'X-Admin-Setup-Token': setup_token,
    }
    payload = {
        'username': 'superadmin',
        'password': 'strong-password-1234',
    }

    resp = client.post('/api/admin/provision', json=payload, headers=headers)
    assert resp.status_code == 201
    assert resp.json == {'created': True, 'username': 'superadmin'}

    # Verify admin exists and password hash works
    with app.app_context():
        admin = Admin.query.filter_by(username='superadmin').first()
        assert admin is not None
        assert admin.verify_password('strong-password-1234')
        assert not admin.verify_password('wrong-password')
        marker = ProvisioningMarker.query.filter_by(marker_name='initial_admin_provisioned').first()
        assert marker is not None

    # Verify the new administrator can log in
    csrf_resp = client.get('/api/csrf')
    login_resp = client.post('/api/admin/auth/login', json={
        'username': 'superadmin',
        'password': 'strong-password-1234'
    }, headers={'X-CSRF-Token': csrf_resp.json['csrf_token']})
    assert login_resp.status_code == 200

def test_admin_provisioning_with_complex_special_characters_password(client, app):
    """Verify passwords with special characters (quotes, backslashes, $, unicode) work securely."""
    setup_token = "secure-setup-token-at-least-32-chars-long!"
    app.config['INITIAL_ADMIN_TOKEN'] = setup_token

    csrf_resp = client.get('/api/csrf')
    headers = {
        'X-CSRF-Token': csrf_resp.json['csrf_token'],
        'X-Admin-Setup-Token': setup_token,
    }
    special_password = 'P@ss"w$ord\\123!&\'<test>🎉'
    resp = client.post('/api/admin/provision', json={
        'username': 'special_admin',
        'password': special_password,
    }, headers=headers)
    assert resp.status_code == 201

    # Verify login with the exact special character password succeeds
    csrf_resp2 = client.get('/api/csrf')
    login_resp = client.post('/api/admin/auth/login', json={
        'username': 'special_admin',
        'password': special_password,
    }, headers={'X-CSRF-Token': csrf_resp2.json['csrf_token']})
    assert login_resp.status_code == 200

def test_admin_provisioning_permanently_disabled_even_if_all_admins_deleted(client, app):
    """Verify that durable marker prevents provisioning even if all admin records are subsequently deleted."""
    setup_token = "secure-setup-token-at-least-32-chars-long!"
    app.config['INITIAL_ADMIN_TOKEN'] = setup_token

    # 1. Successfully provision initial administrator
    csrf_resp = client.get('/api/csrf')
    headers = {
        'X-CSRF-Token': csrf_resp.json['csrf_token'],
        'X-Admin-Setup-Token': setup_token,
    }
    r = client.post('/api/admin/provision', json={'username': 'first_admin', 'password': 'valid-password-12'}, headers=headers)
    assert r.status_code == 201

    with app.app_context():
        assert Admin.query.count() == 1
        assert ProvisioningMarker.query.filter_by(marker_name='initial_admin_provisioned').count() == 1

        # 2. Simulate subsequent deletion of all administrator records
        Admin.query.delete()
        db.session.commit()
        assert Admin.query.count() == 0

    # 3. Attempting to use INITIAL_ADMIN_TOKEN to create another administrator MUST fail
    csrf_resp2 = client.get('/api/csrf')
    headers2 = {
        'X-CSRF-Token': csrf_resp2.json['csrf_token'],
        'X-Admin-Setup-Token': setup_token,
    }
    resp = client.post('/api/admin/provision', json={
        'username': 'attacker_admin',
        'password': 'valid-password-12'
    }, headers=headers2)

    assert resp.status_code == 403
    assert 'permanently disabled' in resp.json['error']
    with app.app_context():
        assert Admin.query.count() == 0

def test_unauthorized_admin_provisioning_attempts(client, app):
    """Verify unauthorized attempts to provision administrator are rejected."""
    setup_token = "secure-setup-token-at-least-32-chars-long!"
    app.config['INITIAL_ADMIN_TOKEN'] = setup_token

    csrf_resp = client.get('/api/csrf')
    csrf_token = csrf_resp.json['csrf_token']

    # 1. Missing setup token
    r1 = client.post('/api/admin/provision', json={
        'username': 'admin1',
        'password': 'password-12345678'
    }, headers={'X-CSRF-Token': csrf_token})
    assert r1.status_code == 401

    # 2. Incorrect setup token
    r2 = client.post('/api/admin/provision', json={
        'username': 'admin2',
        'password': 'password-12345678'
    }, headers={'X-CSRF-Token': csrf_token, 'X-Admin-Setup-Token': 'wrong-token'})
    assert r2.status_code == 401

    # 3. Missing CSRF token
    r3 = client.post('/api/admin/provision', json={
        'username': 'admin3',
        'password': 'password-12345678'
    }, headers={'X-Admin-Setup-Token': setup_token})
    assert r3.status_code == 403

    # 4. Short password (< 12 chars)
    r4 = client.post('/api/admin/provision', json={
        'username': 'admin4',
        'password': 'short'
    }, headers={'X-CSRF-Token': csrf_token, 'X-Admin-Setup-Token': setup_token})
    assert r4.status_code == 400
    assert 'at least 12 characters' in r4.json['error']

    # 5. Blank username
    r5 = client.post('/api/admin/provision', json={
        'username': '   ',
        'password': 'valid-length-password'
    }, headers={'X-CSRF-Token': csrf_token, 'X-Admin-Setup-Token': setup_token})
    assert r5.status_code == 400

    # 6. INITIAL_ADMIN_TOKEN not configured
    app.config['INITIAL_ADMIN_TOKEN'] = ''
    r6 = client.post('/api/admin/provision', json={
        'username': 'admin6',
        'password': 'valid-length-password'
    }, headers={'X-CSRF-Token': csrf_token, 'X-Admin-Setup-Token': setup_token})
    assert r6.status_code == 403
    assert 'not configured' in r6.json['error']

def test_repeated_admin_provisioning_fails(client, app):
    """Verify that once an administrator exists, provisioning becomes permanently unusable."""
    setup_token = "secure-setup-token-at-least-32-chars-long!"
    app.config['INITIAL_ADMIN_TOKEN'] = setup_token

    with app.app_context():
        db.session.add(Admin(username='existing_admin', password_hash=Admin.hash_password('password-12345678')))
        db.session.commit()

    csrf_resp = client.get('/api/csrf')
    csrf_token = csrf_resp.json['csrf_token']

    resp = client.post('/api/admin/provision', json={
        'username': 'new_attempt',
        'password': 'password-12345678'
    }, headers={'X-CSRF-Token': csrf_token, 'X-Admin-Setup-Token': setup_token})

    assert resp.status_code == 403
    assert 'permanently disabled' in resp.json['error']

def test_admin_import_sentences_endpoint(client, app):
    """Verify the authenticated sentence import endpoint."""
    with app.app_context():
        admin = Admin(username='editor', password_hash=Admin.hash_password('password-12345678'))
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    csrf_token = client.get('/api/csrf').json['csrf_token']
    headers = {'X-CSRF-Token': csrf_token}

    # 1. Unauthenticated request rejected
    r1 = client.post('/api/admin/sentences/import', json={}, headers=headers)
    assert r1.status_code == 401

    # 2. Establish admin session and set CSRF
    with client.session_transaction() as sess:
        sess['admin_id'] = admin_id
        sess['csrf_token'] = csrf_token

    # 3. Path traversal / invalid file rejected
    r2 = client.post('/api/admin/sentences/import', json={'filename': '../../etc/passwd'}, headers=headers)
    assert r2.status_code == 400

    r2b = client.post('/api/admin/sentences/import', json={'filename': 'subdir/test.csv'}, headers=headers)
    assert r2b.status_code == 400

    r2c = client.post('/api/admin/sentences/import', json={'filename': '..\\test.csv'}, headers=headers)
    assert r2c.status_code == 400

    r2d = client.post('/api/admin/sentences/import', json={'filename': 'test\0.csv'}, headers=headers)
    assert r2d.status_code == 400

    r3 = client.post('/api/admin/sentences/import', json={'filename': 'nonexistent.csv'}, headers=headers)
    assert r3.status_code == 400

    # 4. Valid dry run import of repository canonical CSV
    from backend import limiter
    limiter.reset()
    r4 = client.post('/api/admin/sentences/import', json={'dry_run': True}, headers=headers)
    assert r4.status_code == 200
    assert r4.json['stats']['imported'] > 0
    with app.app_context():
        assert Sentence.query.count() == 0

    # 5. Idempotent execution: create a dedicated test CSV in repo root
    from pathlib import Path
    root_dir = Path(app.root_path).parent.resolve()
    test_csv = root_dir / 'test_endpoint_import.csv'
    test_csv.write_text("english\nEndpoint sentence 1\nEndpoint sentence 2\n", encoding='utf-8')
    try:
        # First import call: records imported
        limiter.reset()
        r5 = client.post('/api/admin/sentences/import', json={'filename': 'test_endpoint_import.csv', 'column': 'english'}, headers=headers)
        assert r5.status_code == 200
        assert r5.json['stats']['imported'] == 2
        assert r5.json['stats']['duplicates'] == 0
        with app.app_context():
            assert Sentence.query.count() == 2

        # Second import call (accidental rerun): idempotent, 0 imported, 2 duplicates
        limiter.reset()
        r6 = client.post('/api/admin/sentences/import', json={'filename': 'test_endpoint_import.csv', 'column': 'english'}, headers=headers)
        assert r6.status_code == 200
        assert r6.json['stats']['imported'] == 0
        assert r6.json['stats']['duplicates'] == 2
        with app.app_context():
            assert Sentence.query.count() == 2
    finally:
        if test_csv.is_file():
            test_csv.unlink()

def test_cli_create_admin_and_import_sentences(app, tmp_path):
    """Verify CLI commands operate non-interactively when provided options."""
    runner = app.test_cli_runner()

    # CLI create-admin with valid credentials
    result = runner.invoke(args=['create-admin', '--username', 'cli_admin', '--password', 'cli-password-1234'])
    assert result.exit_code == 0
    assert 'Administrator created' in result.output

    with app.app_context():
        admin = Admin.query.filter_by(username='cli_admin').first()
        assert admin is not None
        assert admin.verify_password('cli-password-1234')

    # Duplicate admin creation fails
    result_dup = runner.invoke(args=['create-admin', '--username', 'cli_admin', '--password', 'cli-password-1234'])
    assert result_dup.exit_code != 0
    assert 'already exists' in result_dup.output

    # Short password fails
    result_short = runner.invoke(args=['create-admin', '--username', 'cli_admin2', '--password', 'short'])
    assert result_short.exit_code != 0
    assert 'at least 12 characters' in result_short.output

    # CLI import-sentences
    csv_file = tmp_path / 'cli_import.csv'
    csv_file.write_text("english\nCLI sentence one\nCLI sentence two\n", encoding='utf-8')
    result_import = runner.invoke(args=['import-sentences', str(csv_file), '--column', 'english'])
    assert result_import.exit_code == 0
    assert "'imported': 2" in result_import.output

def test_production_security_behavior(monkeypatch):
    """Verify production security rules for INITIAL_ADMIN_TOKEN and EDGE_PROXY_SECRET."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('COOKIE_SECURE', '1')
    monkeypatch.setenv('ALLOWED_HOSTS', 'example.test')
    monkeypatch.setenv('SECRET_KEY', '7w2Vn9!pQ4#zK8@cR6$mT1^xB3&fH5*j')
    monkeypatch.setenv('EDGE_PROXY_SECRET', '9pV5!cR2@hW7#xM4^tK8&bQ1*zF6$yN3')
    monkeypatch.setenv('RATELIMIT_STORAGE_URI', 'rediss://default:secret@redis.example:6379/0')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db.example.test:5432/postgres?sslmode=require')

    # Weak INITIAL_ADMIN_TOKEN in production is rejected at startup
    monkeypatch.setenv('INITIAL_ADMIN_TOKEN', 'too-short-token')
    with pytest.raises(RuntimeError, match='INITIAL_ADMIN_TOKEN to a unique, high-entropy secret of at least 32 characters'):
        create_app()

    # Valid INITIAL_ADMIN_TOKEN starts cleanly
    monkeypatch.setenv('INITIAL_ADMIN_TOKEN', 'k8#Pz2!vL9$mR4^wX1&tN7@bQ5*jY3(cF6')
    prod_app = create_app()
    assert prod_app.config['INITIAL_ADMIN_TOKEN'] == 'k8#Pz2!vL9$mR4^wX1&tN7@bQ5*jY3(cF6'

    # Verify edge proxy enforcement in production
    client = prod_app.test_client()
    # 1. Missing X-Deployment-Proxy header returns 403
    resp_no_proxy = client.post('/api/admin/provision', json={'username': 'admin', 'password': 'password-12345678'}, headers={'Host': 'example.test'})
    assert resp_no_proxy.status_code == 403
    assert 'Use the application API endpoint' in resp_no_proxy.json['error']

    # 2. Invalid X-Deployment-Proxy returns 403
    resp_bad_proxy = client.get('/api/csrf', headers={'Host': 'example.test', 'X-Deployment-Proxy': 'wrong-proxy-secret-at-least-32-chars!'})
    assert resp_bad_proxy.status_code == 403
    assert 'Use the application API endpoint' in resp_bad_proxy.json['error']

    # 3. Valid X-Deployment-Proxy allows request to proceed through edge proxy guard
    # We bypass redis connection in test client by mocking or checking edge proxy check
    with prod_app.test_request_context('/api/csrf', headers={'Host': 'example.test', 'X-Deployment-Proxy': '9pV5!cR2@hW7#xM4^tK8&bQ1*zF6$yN3'}):
        # require_edge_proxy returns None when header matches
        assert prod_app.preprocess_request() is None

    # 4. Public /api/health does NOT require X-Deployment-Proxy
    resp_health = client.get('/api/health', headers={'Host': 'example.test'})
    assert resp_health.status_code == 200
    assert resp_health.json == {'status': 'ok'}
