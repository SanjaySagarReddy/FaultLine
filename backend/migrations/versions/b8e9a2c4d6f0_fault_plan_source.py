"""Track the source of each effective fault plan.

Revision ID: b8e9a2c4d6f0
Revises: 337a76afb704
"""
from alembic import op
import sqlalchemy as sa

revision = "b8e9a2c4d6f0"
down_revision = "337a76afb704"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("fault_plan_source", sa.String(length=20),
                                    nullable=False, server_default="legacy_unknown"))


def downgrade():
    op.drop_column("runs", "fault_plan_source")
