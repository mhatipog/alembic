"""Regression tests for indexes built during a move-and-copy migration."""

import pytest

from sqlalchemy import Column
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy import Integer
from sqlalchemy import literal_column
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import column

from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize(
    "columns, expected",
    [
        (["name"], "(name)"),
        ([text("lower(name)")], "(lower(name))"),
        ([literal_column("name DESC")], "(name DESC)"),
        ([func.lower(column("name"))], "(lower(name))"),
        ([column("name").desc()], "(name DESC)"),
        (["id", literal_column("name DESC")], "(id, name DESC)"),
    ],
)
def test_batch_recreate_preserves_new_index_expressions(columns, expected):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        table = Table(
            "users",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("name", String),
        )
        table.create(connection)
        connection.exec_driver_sql(
            "INSERT INTO users (name) VALUES ('Alice'), ('bob')"
        )
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("users", recreate="always") as batch:
            batch.add_column(Column("note", String))
            batch.create_index("ix_users_expression", columns)

        ddl = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='ix_users_expression'"
        ).scalar_one()
        assert ddl.endswith(expected), ddl
        assert connection.exec_driver_sql(
            "SELECT id, name, note FROM users ORDER BY id"
        ).all() == [(1, "Alice", None), (2, "bob", None)]
        assert {c[1] for c in connection.exec_driver_sql(
            "PRAGMA table_info(users)"
        )} == {"id", "name", "note"}


def test_batch_recreate_functional_unique_index_is_enforced():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Table("users", MetaData(), Column("name", String)).create(connection)
        connection.exec_driver_sql("INSERT INTO users(name) VALUES ('Alice')")
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("users", recreate="always") as batch:
            batch.create_index(
                "uq_users_lower_name", [text("lower(name)")], unique=True
            )

        ddl = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='uq_users_lower_name'"
        ).scalar_one()
        assert ddl == (
            "CREATE UNIQUE INDEX uq_users_lower_name ON users (lower(name))"
        )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO users(name) VALUES ('ALICE')"
            )


def test_batch_recreate_new_partial_expression_index():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Table(
            "users", MetaData(), Column("id", Integer, primary_key=True),
            Column("name", String),
        ).create(connection)
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("users", recreate="always") as batch:
            batch.create_index(
                "ix_users_present_name", [func.lower(column("name"))],
                sqlite_where=text("name IS NOT NULL"),
            )
        ddl = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='ix_users_present_name'"
        ).scalar_one()
        assert ddl == (
            "CREATE INDEX ix_users_present_name ON users (lower(name)) "
            "WHERE name IS NOT NULL"
        )


def test_batch_recreate_offline_expression_index():
    from io import StringIO

    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="sqlite",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    operations = Operations(context)
    table = Table(
        "users", MetaData(), Column("id", Integer, primary_key=True),
        Column("name", String),
    )
    with operations.batch_alter_table(
        "users", recreate="always", copy_from=table,
    ) as batch:
        batch.create_index(
            "ix_users_lower", [func.lower(column("name"))], unique=True,
        )
    output = buffer.getvalue()
    assert (
        "CREATE UNIQUE INDEX ix_users_lower ON users (lower(name))"
        in output
    )


def test_batch_recreate_expression_uses_existing_table_column():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        table = Table(
            "users", MetaData(), Column("id", Integer, primary_key=True),
            Column("name", String),
        )
        table.create(connection)
        connection.exec_driver_sql("CREATE INDEX ix_users_id ON users (id)")
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("users", recreate="always") as batch:
            batch.create_index(
                "ix_users_lower_name", [func.lower(table.c.name)],
            )
        indexes = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'ix_users_%' ORDER BY name"
        ).all()
        assert indexes == [
            ("ix_users_id", "CREATE INDEX ix_users_id ON users (id)"),
            (
                "ix_users_lower_name",
                "CREATE INDEX ix_users_lower_name ON users (lower(name))",
            ),
        ]


def test_batch_recreate_index_on_new_column():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Table(
            "users", MetaData(), Column("id", Integer, primary_key=True)
        ).create(connection)
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("users", recreate="always") as batch:
            batch.add_column(Column("name", String))
            batch.create_index("ix_users_name", ["name"])
        ddl = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='ix_users_name'"
        ).scalar_one()
        assert ddl == "CREATE INDEX ix_users_name ON users (name)"


def test_batch_recreate_multiple_textual_indexes_do_not_conflict():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Table("users", MetaData(), Column("name", String)).create(connection)
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("users", recreate="always") as batch:
            batch.create_index("ix_users_name", [text("name")])
            batch.create_index("ix_users_lower_a", [text("lower(name)")])
            batch.create_index("ix_users_lower_b", [text("lower(name)")])
        indexes = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'ix_users_%' ORDER BY name"
        ).all()
        assert indexes == [
            (\n                "ix_users_lower_a",\n                "CREATE INDEX ix_users_lower_a ON users (lower(name))",\n            ),
            (\n                "ix_users_lower_b",\n                "CREATE INDEX ix_users_lower_b ON users (lower(name))",\n            ),
            ("ix_users_name", "CREATE INDEX ix_users_name ON users (name)"),
        ]
