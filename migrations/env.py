from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import engine_from_config, pool

# Imported for their side effect: registering tables on Base.metadata.
import branchiv_org.corpus  # noqa: F401
import branchiv_org.tracker.refusals  # noqa: F401
from branchiv_org.db import Base, database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL comes from Settings (.env / environment), never from alembic.ini.
config.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Let autogenerate / `alembic check` reflect pgvector columns.
        connection.dialect.ischema_names["vector"] = Vector
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
