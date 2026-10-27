import pytest
from backend import create_app, db

@pytest.fixture
def app():
    app=create_app({'TESTING':True,'SECRET_KEY':'test-key','SQLALCHEMY_DATABASE_URI':'sqlite://','SQLALCHEMY_ENGINE_OPTIONS':{'connect_args':{'check_same_thread':False}}})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove(); db.drop_all()

@pytest.fixture
def client(app): return app.test_client()
