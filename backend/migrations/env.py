from alembic import context

from app.database import make_engine

engine = make_engine()
try:
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=None, version_table_schema="workspace"
        )
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
