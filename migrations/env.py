"""Alembic Environment: URL и метаданные — из конфигурации TokLib (docs/09 §9.12)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from toklib_core.config import load_settings
from toklib_core.db import Base, make_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = load_settings()
# URL может быть задан вызывающим (тесты, api: setup_database) — не перетираем
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.server.db_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Через фабруку TokLib: она создаёт родительский каталог файлового SQLite
    # («unable to open database file» на свежем TOKLIB_DATA_DIR в CI/контейнере)
    # и ставит прагмы WAL/busy_timeout.
    connectable = make_engine(config.get_main_option("sqlalchemy.url"))
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-friendly ALTER
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
