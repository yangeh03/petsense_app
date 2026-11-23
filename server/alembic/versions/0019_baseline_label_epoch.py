"""Baseline label epoch: invalidate pre-fix derived action caches.

接 W10 的 0018（单 head：0017 → 0018 → 0019）。走动秒数读法修复（同时识别
walking/走动）部署后，旧 pet_baselines 行即使水位不变也保留错误范围；新增
label_epoch 列并以 NULL 表示修复前缓存，ensure_baseline 惰性重算一次并落当前
纪元。只作废旧派生缓存：遥测、agent_learning_days 与学习进度全部不动。
"""
from alembic import op
import sqlalchemy as sa
revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pet_baselines', sa.Column('label_epoch', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('pet_baselines', 'label_epoch')
