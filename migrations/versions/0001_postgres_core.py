"""Create shared Nourish account and nutrition catalogue tables."""
from alembic import op
from database.models import Base, catalogue
revision = "0001_postgres_core"
down_revision = None
branch_labels = None
depends_on = None
def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    catalogue.create_all(bind=bind, checkfirst=True)
def downgrade():
    bind = op.get_bind()
    catalogue.drop_all(bind=bind, checkfirst=True)
    Base.metadata.drop_all(bind=bind, checkfirst=True)
