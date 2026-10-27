"""Create the initial Gede’uffa archive schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision='0001_initial'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('contributor',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('full_name',sa.String(160),nullable=False),
        sa.Column('address_or_community',sa.String(240),nullable=False),sa.Column('occupation',sa.String(160),nullable=False),
        sa.Column('archive_consent',sa.Boolean(),nullable=False),sa.Column('ai_research_consent',sa.Boolean(),nullable=False),
        sa.Column('consented_at',sa.DateTime(timezone=True)),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('admin',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('username',sa.String(80),nullable=False,unique=True),
        sa.Column('password_hash',sa.String(255),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('contributor_session',sa.Column('token_hash',sa.String(64),primary_key=True),
        sa.Column('contributor_id',sa.Integer(),sa.ForeignKey('contributor.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('ended_at',sa.DateTime(timezone=True)))
    op.create_table('sentence',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('source_key',sa.String(64),nullable=False,unique=True),
        sa.Column('english_text',sa.Text(),nullable=False),sa.Column('is_active',sa.Boolean(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('assignment',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('contributor_id',sa.Integer(),sa.ForeignKey('contributor.id'),nullable=False),
        sa.Column('sentence_id',sa.Integer(),sa.ForeignKey('sentence.id'),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('assigned_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('submitted_at',sa.DateTime(timezone=True)),sa.Column('idempotency_key',sa.String(100),unique=True),
        sa.UniqueConstraint('id','contributor_id','sentence_id',name='uq_assignment_identity'),
        sa.CheckConstraint("status in ('assigned','submitted','expired','cancelled')",name='assignment_status_valid'))
    op.create_index('ix_assignment_sentence_status','assignment',['sentence_id','status'])
    op.create_index('ix_assignment_contributor_status','assignment',['contributor_id','status'])
    op.create_index('uq_active_assignment_contributor_sentence','assignment',['contributor_id','sentence_id'],unique=True,
        postgresql_where=sa.text("status = 'assigned'"),sqlite_where=sa.text("status = 'assigned'"))
    op.create_table('translation',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('assignment_id',sa.Integer(),sa.ForeignKey('assignment.id'),nullable=False,unique=True),
        sa.Column('contributor_id',sa.Integer(),sa.ForeignKey('contributor.id'),nullable=False),
        sa.Column('sentence_id',sa.Integer(),sa.ForeignKey('sentence.id'),nullable=False),sa.Column('gedeuffa_text',sa.Text(),nullable=False),
        sa.Column('review_status',sa.String(16),nullable=False),sa.Column('approval_kind',sa.String(16)),
        sa.Column('submitted_at',sa.DateTime(timezone=True),nullable=False),sa.Column('reviewed_at',sa.DateTime(timezone=True)),
        sa.Column('reviewed_by',sa.Integer(),sa.ForeignKey('admin.id')),sa.Column('rejection_reason',sa.Text()),
        sa.UniqueConstraint('contributor_id','sentence_id',name='uq_translation_contributor_sentence'),
        sa.ForeignKeyConstraint(['assignment_id','contributor_id','sentence_id'],['assignment.id','assignment.contributor_id','assignment.sentence_id'],name='fk_translation_assignment_owner_sentence'),
        sa.CheckConstraint("review_status in ('pending','approved','rejected')",name='translation_review_status_valid'),
        sa.CheckConstraint("approval_kind is null or approval_kind in ('primary','alternative')",name='approval_kind_valid'))
    op.create_index('ix_translation_sentence_id','translation',['sentence_id'])
    op.create_index('uq_primary_sentence','translation',['sentence_id'],unique=True,
        postgresql_where=sa.text("approval_kind = 'primary'"),sqlite_where=sa.text("approval_kind = 'primary'"))
    op.create_table('audit_log',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('admin_id',sa.Integer(),sa.ForeignKey('admin.id'),nullable=False),
        sa.Column('translation_id',sa.Integer(),sa.ForeignKey('translation.id'),nullable=False),sa.Column('action',sa.String(32),nullable=False),
        sa.Column('detail',sa.Text()),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    for table in ('audit_log','translation','assignment','contributor_session','sentence','admin','contributor'):
        op.drop_table(table)
