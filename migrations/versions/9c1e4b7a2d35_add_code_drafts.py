"""add code_drafts table for the problem workbench

Revision ID: 9c1e4b7a2d35
Revises: 782d141f88ad
Create Date: 2026-11-08 19:55:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '9c1e4b7a2d35'
down_revision: str | None = '782d141f88ad'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'code_drafts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('problem_id', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=40), nullable=False),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('problem_id', name='uq_code_draft_problem'),
    )
    op.create_index('ix_code_drafts_problem', 'code_drafts', ['problem_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_code_drafts_problem', table_name='code_drafts')
    op.drop_table('code_drafts')
