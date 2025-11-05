import logging
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from typing import Iterable
from sqlmodel import SQLModel, create_engine
import hub.util as util

### How to make a new schema version of the database
# 1. update your SQLModel classes
# 2. increment db_schema_version below
# 3. run bbhub and verify that it does this correctly:
#    * copy `data.sqlite` to `data.1.sqlite`
#    * build a fresh schema file
#    * copy column intersections from the old DB
#    * atomically replace the old file
# Docs: https://sqlite.org/pragma.html#pragma_user_version

db_schema_version = 4

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # will be throttled by handler log level (file, console)


@contextmanager
def sqlite_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def db_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'''PRAGMA table_info('{table}');''').fetchall()
    return [r[1] for r in rows]  # column name


def intersection(a: Iterable[str], b: Iterable[str]) -> list[str]:
    sa, sb = set(a), set(b)
    return [c for c in a if c in sb]  # preserve order from 'a'


def copy_data_from_old_db_to_new(new_db_path: str, old_db_path: str) -> None:
    with sqlite_conn(new_db_path) as conn:
        # https://sqlite.org/lang_attach.html
        conn.execute(f'''ATTACH DATABASE ? AS old_db;''', (old_db_path,))
        try:
            rows = conn.execute(  # https://sqlite.org/schematab.html
                '''SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%';'''
            ).fetchall()
            new_tables = [r[0] for r in rows]

            for table in new_tables:  # columns for tables in main (new) and old_db (old)
                new_cols = db_table_columns(conn, table)
                old_cols = db_table_columns(conn, f'old_db.{table}')
                common = intersection(new_cols, old_cols)
                if not common:
                    continue
                cols_csv = ', '.join([f'"{c}"' for c in common])
                conn.execute(
                    f'''INSERT INTO '{table}' ({cols_csv})
                    SELECT {cols_csv} FROM old_db.'{table}';'''
                )
        finally:
            conn.execute('''DETACH DATABASE old_db;''')


def migrate(db_path: str) -> None:
    if not os.path.exists(db_path):
        raise util.Berror(f"B31778 cannot read database: {db_path}")
    with sqlite_conn(db_path) as conn:
        cur = conn.execute('''PRAGMA user_version;''')
        row = cur.fetchone()
        current_version = int(row[0] if row and row[0] is not None else 0)
    if current_version >= db_schema_version:
        return
    logger.info(f"B49255 migrating database from version {current_version} to {db_schema_version}")
    with tempfile.NamedTemporaryFile(
        dir=os.path.dirname(db_path),  # in the same directory
        prefix='data-',
        suffix=".sqlite",
        delete=False,
        mode="w",
    ) as f:
        tmp_path = f.name
    engine = create_engine(f'sqlite:///{tmp_path}')
    SQLModel.metadata.create_all(engine)
    with sqlite_conn(tmp_path) as conn:
        conn.execute(f'PRAGMA user_version = {int(db_schema_version)};')
    copy_data_from_old_db_to_new(tmp_path, db_path)
    util.rotate_backups(db_path, tmp_path)
