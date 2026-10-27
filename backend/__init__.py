import os
import secrets
import hmac
import ipaddress
from urllib.parse import parse_qs, urlparse
from datetime import timedelta
from flask import Flask, jsonify, request, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

db = SQLAlchemy()
migrate = Migrate()

def rate_limit_identity():
    # Only trust the client IP passed by the Pages Function when its shared
    # secret matches. Direct requests use Flask's socket address.
    expected = os.getenv('EDGE_PROXY_SECRET', '')
    supplied = request.headers.get('X-Deployment-Proxy', '')
    client_ip = request.headers.get('X-Pilot-Client-IP', '')
    if expected and supplied and hmac.compare_digest(supplied, expected):
        try:
            return str(ipaddress.ip_address(client_ip))
        except ValueError:
            pass
    return get_remote_address()

limiter = Limiter(
    key_func=rate_limit_identity,
    default_limits=['300 per hour'],
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
    in_memory_fallback_enabled=False,
)

def create_app(test_config=None):
    app = Flask(__name__)
    production = os.getenv('APP_ENV') == 'production'
    secret = os.getenv('SECRET_KEY')
    if production and (not secret or secret == 'replace-with-a-long-random-secret' or len(secret) < 32 or len(set(secret)) < 12):
        raise RuntimeError('Set a unique, high-entropy SECRET_KEY of at least 32 characters before starting production.')
    if production and os.getenv('COOKIE_SECURE') != '1':
        raise RuntimeError('Set COOKIE_SECURE=1 and serve the application over HTTPS in production.')
    allowed_hosts = [host.strip() for host in os.getenv('ALLOWED_HOSTS', '').split(',') if host.strip()]
    if production and (not allowed_hosts or any(host == '*' or '://' in host or '/' in host or any(char.isspace() for char in host) for host in allowed_hosts)):
        raise RuntimeError('Set ALLOWED_HOSTS to exact public hostnames without schemes, paths, or wildcards before starting production.')
    edge_proxy_secret = os.getenv('EDGE_PROXY_SECRET', '')
    if production and (len(edge_proxy_secret) < 32 or len(set(edge_proxy_secret)) < 12):
        raise RuntimeError('Set EDGE_PROXY_SECRET to a unique, high-entropy secret of at least 32 characters before starting production.')
    rate_limit_storage = os.getenv('RATELIMIT_STORAGE_URI', '')
    parsed_redis_url = urlparse(rate_limit_storage)
    if production and (parsed_redis_url.scheme != 'rediss' or not parsed_redis_url.hostname or not parsed_redis_url.password):
        raise RuntimeError('Set RATELIMIT_STORAGE_URI to a TLS-protected Redis URL (rediss://) before starting production.')
    database_url = os.getenv('DATABASE_URL', '')
    if production:
        parsed_database_url = urlparse(database_url)
        ssl_modes = parse_qs(parsed_database_url.query).get('sslmode', [])
        if parsed_database_url.scheme not in {'postgresql', 'postgresql+psycopg'} or not parsed_database_url.hostname or not parsed_database_url.path.strip('/'):
            raise RuntimeError('Set DATABASE_URL to the production PostgreSQL service.')
        if not ssl_modes or ssl_modes[0] not in {'require', 'verify-ca', 'verify-full'}:
            raise RuntimeError('Set DATABASE_URL with sslmode=require or stronger for encrypted PostgreSQL connections.')
        if parsed_database_url.scheme == 'postgresql':
            # Accept the standard PostgreSQL URI format so the same value can
            # be used by PostgreSQL utilities such as pg_dump and pg_restore.
            database_url = database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    try:
        trusted_proxy_count = int(os.getenv('TRUSTED_PROXY_COUNT', '0'))
    except ValueError as error:
        raise RuntimeError('TRUSTED_PROXY_COUNT must be a non-negative integer.') from error
    if trusted_proxy_count < 0:
        raise RuntimeError('TRUSTED_PROXY_COUNT must be a non-negative integer.')
    if trusted_proxy_count:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_count, x_proto=trusted_proxy_count, x_host=trusted_proxy_count)
    app.config.update(
        SECRET_KEY=secret or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=database_url or 'sqlite:///gedeuffa.sqlite',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        ASSIGNMENT_TTL_HOURS=int(os.getenv('ASSIGNMENT_TTL_HOURS', '24')),
        CONTRIBUTOR_SESSION_TTL_DAYS=int(os.getenv('CONTRIBUTOR_SESSION_TTL_DAYS', '30')),
        SENTENCE_CSV_COLUMN=os.getenv('SENTENCE_CSV_COLUMN', 'english_text'),
        MAX_CONTENT_LENGTH=32 * 1024,
        MAX_CSV_BYTES=int(os.getenv('MAX_CSV_BYTES', str(10 * 1024 * 1024))),
        MAX_CSV_ROWS=int(os.getenv('MAX_CSV_ROWS', '100000')),
        TRUSTED_HOSTS=allowed_hosts or None,
        EDGE_PROXY_SECRET=edge_proxy_secret,
    )
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(app.root_path, 'migrations'))
    limiter.init_app(app)
    from . import models
    from .routes import api
    app.register_blueprint(api, url_prefix='/api')

    @app.before_request
    def require_edge_proxy():
        if production and request.path.startswith('/api/') and request.endpoint != 'api.health':
            supplied = request.headers.get('X-Deployment-Proxy', '')
            if not supplied or not hmac.compare_digest(supplied, app.config['EDGE_PROXY_SECRET']):
                return jsonify(error='Use the application API endpoint.'), 403

    @app.before_request
    def csrf_guard():
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and request.endpoint != 'api.health':
            token = session.get('csrf_token')
            if not token or request.headers.get('X-CSRF-Token') != token:
                return jsonify(error='Invalid CSRF token'), 403

    @app.after_request
    def security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
            "object-src 'none'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com",
        )
        if request.path.startswith('/api/'):
            response.headers.setdefault('Cache-Control', 'no-store')
        if production:
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error='Request body exceeds the 32 KiB limit.'), 413

    @app.get('/api/csrf')
    def csrf():
        import secrets
        token = session.setdefault('csrf_token', secrets.token_urlsafe(32))
        return jsonify(csrf_token=token)

    @app.cli.command('init-db')
    def init_db():
        db.create_all()
        print('Database tables created.')

    @app.cli.command('create-admin')
    def create_admin():
        from getpass import getpass
        from .models import Admin
        username = input('Admin username: ').strip()
        if not username:
            raise SystemExit('Username cannot be blank.')
        password = getpass('Admin password (12+ characters): ')
        if len(password) < 12:
            raise SystemExit('Password must be at least 12 characters.')
        if Admin.query.filter_by(username=username).first():
            raise SystemExit('That username already exists.')
        db.session.add(Admin(username=username, password_hash=Admin.hash_password(password)))
        db.session.commit()
        print('Administrator created.')

    @app.cli.command('import-sentences')
    @__import__('click').argument('path', type=__import__('click').Path(exists=True, dir_okay=False))
    @__import__('click').option('--dry-run', is_flag=True, help='Validate and report without storing rows.')
    def import_sentences(path, dry_run):
        from .importer import import_csv
        stats = import_csv(path, app.config['SENTENCE_CSV_COLUMN'], dry_run=dry_run)
        print(stats)

    @app.get('/')
    @app.get('/<path:frontend_path>')
    def frontend(frontend_path='index.html'):
        from flask import send_from_directory
        from pathlib import Path
        if frontend_path.startswith('api/'):
            return jsonify(error='API route not found.'), 404
        dist = Path(app.root_path).parent / 'frontend-dist'
        target = dist / frontend_path
        return send_from_directory(dist, frontend_path if target.is_file() else 'index.html')

    return app
