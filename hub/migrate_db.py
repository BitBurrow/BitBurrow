import logging
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from typing import Iterable
from sqlmodel import SQLModel, create_engine
import hub.util as util

### How to make a new schema version of the database
# 1. update your SQLModel classes (note that the migration below will not fill in default
#      values for new fields)
# 2. increment db_schema_version below
# 3. run bbhub and verify that it does this correctly:
#    * copy `data.sqlite` to `data.1.sqlite`
#    * build a fresh schema file
#    * copy column intersections from the old DB
#    * atomically replace the old file
# Docs: https://sqlite.org/pragma.html#pragma_user_version

db_schema_version = 24

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


def copy_data_from_old_db_to_new(new_db_path: str, old_db_path: str) -> None:
    with sqlite_conn(new_db_path) as conn:
        # https://sqlite.org/lang_attach.html
        conn.execute('''ATTACH DATABASE ? AS old_db;''', (old_db_path,))
        try:
            rows = conn.execute(  # https://sqlite.org/schematab.html
                '''SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%';'''
            ).fetchall()
            new_tables = [r[0] for r in rows]
            for table in new_tables:
                new_info = conn.execute(f'''PRAGMA table_info("{table}");''').fetchall()
                old_info = conn.execute(f'''PRAGMA old_db.table_info("{table}");''').fetchall()
                new_cols = [r[1] for r in new_info]
                old_cols = {r[1] for r in old_info}
                select_exprs = list()  # build SELECT list aligned with new_cols
                for cid, name, coltype, notnull, dflt_value, _pk in new_info:
                    if name in old_cols:
                        select_exprs.append(f'"{name}"')
                        continue
                    if dflt_value is not None:
                        select_exprs.append(dflt_value)  # dflt_value is already SQL-quoted
                    elif notnull:
                        t = (coltype or '').upper()
                        if 'CHAR' in t or 'CLOB' in t or 'TEXT' in t:
                            select_exprs.append("''")
                        elif 'INT' in t or 'REAL' in t or 'FLOA' in t or 'DOUB' in t or 'NUM' in t:
                            select_exprs.append("0")
                        elif 'BLOB' in t:
                            select_exprs.append("X'00'")
                        else:
                            select_exprs.append("''")
                    else:
                        select_exprs.append("NULL")
                cols_csv = ', '.join(f'"{c}"' for c in new_cols)
                selects_csv = ', '.join(select_exprs)
                # only attempt to copy if the table existed in old_db
                old_exists = conn.execute(
                    '''SELECT 1 FROM old_db.sqlite_master
                       WHERE type='table' AND name=?;''',
                    (table,),
                ).fetchone()
                if not old_exists:
                    continue
                conn.execute(
                    f'''INSERT INTO "{table}" ({cols_csv})
                    SELECT {selects_csv} FROM old_db."{table}";'''
                )
        finally:
            conn.commit()
            conn.execute('''DETACH DATABASE old_db;''')


def migrate(db_path: str) -> None:
    if not os.path.exists(db_path):
        return  # not an error because it may have not been created yet
    with sqlite_conn(db_path) as conn:
        row = conn.execute('''PRAGMA user_version;''').fetchone()
        current_version = int(row[0] if row and row[0] is not None else 0)
    if current_version >= db_schema_version:
        return
    logger.info(f"B49255 migrating database from version {current_version} to {db_schema_version}")
    if current_version == 22:  # version 22 → 23 renamed 2 data fields
        with sqlite_conn(db_path) as conn:
            conn.execute('''ALTER TABLE "Intf" RENAME COLUMN "privkey" TO "wg_privkey";''')
            conn.execute('''ALTER TABLE "Intf" RENAME COLUMN "pubkey" TO "wg_pubkey";''')
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
        conn.execute(f'''PRAGMA user_version = {int(db_schema_version)};''')
    copy_data_from_old_db_to_new(tmp_path, db_path)
    util.rotate_backups(db_path, tmp_path)
