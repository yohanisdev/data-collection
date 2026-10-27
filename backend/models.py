from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import UniqueConstraint, Index, CheckConstraint, ForeignKeyConstraint
from . import db

def now_utc(): return datetime.now(timezone.utc)

class Contributor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(160), nullable=False)
    address_or_community = db.Column(db.String(240), nullable=False)
    occupation = db.Column(db.String(160), nullable=False)
    archive_consent = db.Column(db.Boolean, nullable=False, default=False)
    ai_research_consent = db.Column(db.Boolean, nullable=False, default=False)
    consented_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)

class ContributorSession(db.Model):
    token_hash = db.Column(db.String(64), primary_key=True)
    contributor_id = db.Column(db.Integer, db.ForeignKey('contributor.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True))

class Sentence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_key = db.Column(db.String(64), unique=True, nullable=False)
    english_text = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contributor_id = db.Column(db.Integer, db.ForeignKey('contributor.id'), nullable=False)
    sentence_id = db.Column(db.Integer, db.ForeignKey('sentence.id'), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='assigned')
    assigned_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True))
    idempotency_key = db.Column(db.String(100), unique=True)
    sentence = db.relationship('Sentence')
    __table_args__ = (
        CheckConstraint("status in ('assigned','submitted','expired','cancelled')", name='assignment_status_valid'),
        UniqueConstraint('id','contributor_id','sentence_id', name='uq_assignment_identity'),
        Index('uq_active_assignment_contributor_sentence','contributor_id','sentence_id',unique=True,postgresql_where=(status == 'assigned'),sqlite_where=(status == 'assigned')),
        Index('ix_assignment_sentence_status', 'sentence_id', 'status'),
        Index('ix_assignment_contributor_status', 'contributor_id', 'status'),
    )

class Translation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False, unique=True)
    contributor_id = db.Column(db.Integer, db.ForeignKey('contributor.id'), nullable=False)
    sentence_id = db.Column(db.Integer, db.ForeignKey('sentence.id'), nullable=False)
    gedeuffa_text = db.Column(db.Text, nullable=False)
    review_status = db.Column(db.String(16), nullable=False, default='pending')
    approval_kind = db.Column(db.String(16))
    submitted_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    reviewed_at = db.Column(db.DateTime(timezone=True))
    reviewed_by = db.Column(db.Integer, db.ForeignKey('admin.id'))
    rejection_reason = db.Column(db.Text)
    __table_args__ = (
        ForeignKeyConstraint(['assignment_id','contributor_id','sentence_id'], ['assignment.id','assignment.contributor_id','assignment.sentence_id'], name='fk_translation_assignment_owner_sentence'),
        UniqueConstraint('contributor_id', 'sentence_id', name='uq_translation_contributor_sentence'),
        CheckConstraint("review_status in ('pending','approved','rejected')", name='translation_review_status_valid'),
        CheckConstraint("approval_kind is null or approval_kind in ('primary','alternative')", name='approval_kind_valid'),
        Index('ix_translation_sentence_id','sentence_id'),
        Index('uq_primary_sentence', 'sentence_id', unique=True, postgresql_where=(approval_kind == 'primary'), sqlite_where=(approval_kind == 'primary')),
    )

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
    @staticmethod
    def hash_password(password): return generate_password_hash(password)
    def verify_password(self, password): return check_password_hash(self.password_hash, password)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=False)
    translation_id = db.Column(db.Integer, db.ForeignKey('translation.id'), nullable=False)
    action = db.Column(db.String(32), nullable=False)
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=now_utc, nullable=False)
