"""empty message

Revision ID: 7a04b531d821
Revises: 
Create Date: 2018-01-03 23:57:52.418816

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7a04b531d821'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('product',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=True),
    sa.Column('vendor', sa.Integer(), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('detail_url', sa.String(length=128), nullable=True),
    sa.Column('image_url', sa.String(length=128), nullable=True),
    sa.Column('price', sa.Integer(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('brand', sa.String(length=64), nullable=True),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('upc', sa.String(length=12), nullable=True),
    sa.Column('quantity_desc', sa.String(length=64), nullable=True),
    sa.Column('data', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['vendor'], ['vendor.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('vendor', 'sku')
    )
    op.create_index(op.f('ix_product_brand'), 'product', ['brand'], unique=False)
    op.create_index(op.f('ix_product_model'), 'product', ['model'], unique=False)
    op.create_index(op.f('ix_product_price'), 'product', ['price'], unique=False)
    op.create_index(op.f('ix_product_sku'), 'product', ['sku'], unique=False)
    op.create_index(op.f('ix_product_title'), 'product', ['title'], unique=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_product_title'), table_name='product')
    op.drop_index(op.f('ix_product_sku'), table_name='product')
    op.drop_index(op.f('ix_product_price'), table_name='product')
    op.drop_index(op.f('ix_product_model'), table_name='product')
    op.drop_index(op.f('ix_product_brand'), table_name='product')
    op.drop_table('product')
    # ### end Alembic commands ###