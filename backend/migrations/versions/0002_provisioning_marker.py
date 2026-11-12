"""Create the provisioning marker table.

Revision ID: 0002_provisioning_marker
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_provisioning_marker'
down_revision = '0001_initial'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'provisioning_marker',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('marker_name', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False)
    )

def downgrade():
    op.drop_table('provisioning_marker')
