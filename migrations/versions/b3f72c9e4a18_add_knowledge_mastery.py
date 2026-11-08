"""add knowledge_mastery table for the personal ability profile

Revision ID: b3f72c9e4a18
Revises: 9c1e4b7a2d35
Create Date: 2026-11-08 20:05:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b3f72c9e4a18'
down_revision: str | None = '9c1e4b7a2d35'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'knowledge_mastery',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['node_id'], ['knowledge_nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('node_id', name='uq_knowledge_mastery_node'),
    )
    op.create_index('ix_knowledge_mastery_node', 'knowledge_mastery', ['node_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_knowledge_mastery_node', table_name='knowledge_mastery')
    op.drop_table('knowledge_mastery')
