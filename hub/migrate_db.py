from datetime import datetime as DateTime, timedelta as TimeDelta, timezone as TimeZone
import logging
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from sqlalchemy import update
from sqlmodel import SQLModel, create_engine, Session, select
import hub.config as conf
import hub.db as db
import hub.login_key as lk
import hub.util as util

### How to make a new schema version of the database
# 1. update your SQLModel classes (note that the migration below will not fill in default
#      values for new fields)
# 2. increment db_schema_version below
# 3. append data/schema changes to migrate_versions(); see version 48 → 49
# 4. run bbhub and verify that it does this correctly:
#    * copy `data.sqlite` to `data.1.sqlite`
#    * build a fresh schema file
#    * copy column intersections from the old DB
#    * atomically replace the old file
# Docs: https://sqlite.org/pragma.html#pragma_user_version

db_schema_version = 49

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
                    if (coltype or '').upper().startswith('DATETIME'):
                        select_exprs.append("'1970-01-01 00:00:00.000000'")
                    elif dflt_value is not None:
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
                conn.execute(f'''INSERT INTO "{table}" ({cols_csv})
                    SELECT {selects_csv} FROM old_db."{table}";''')
        finally:
            conn.commit()
            conn.execute('''DETACH DATABASE old_db;''')


@contextmanager
def temporary_db(db_path: str):
    """Reserve a private temporary DB beside the original, and remove it on exit."""
    with tempfile.NamedTemporaryFile(
        dir=os.path.dirname(db_path), prefix='data-', suffix='.sqlite', delete=False
    ) as f:
        tmp_path = f.name
    try:
        yield tmp_path
    finally:
        for suffix in ('-journal', '-wal', '-shm', ''):
            try:
                os.remove(tmp_path + suffix)
            except FileNotFoundError:
                pass


def migrate_versions(db_path: str, version: int, target: int, engine) -> None:
    """Run migration sections with version < section_version <= target."""
    # migrate() calls this twice: 49+ on the stored schema, then pre-49 on the rebuilt schema.
    if version < 23 <= target:  # version 22 → 23: renamed 2 Intf data fields
        with sqlite_conn(db_path) as conn:
            conn.execute('''ALTER TABLE "Intf" RENAME COLUMN "privkey" TO "wg_privkey";''')
            conn.execute('''ALTER TABLE "Intf" RENAME COLUMN "pubkey" TO "wg_pubkey";''')
        version = 23
    if version < 27 <= target:  # version 26 → 27: added Device.subd
        with Session(engine) as session:
            statement = select(db.Intf.device_id).where(db.Intf.base_intf_id == db.hub_id)
            device_ids = session.exec(statement.distinct()).all()
            subd = lambda _: (lk.generate_login_key_letters(1) + lk.generate_login_key(3)).lower()
            for device_id in device_ids:
                device = session.get(db.Device, device_id)
                if device.subd is not None:
                    continue
                db.set_unique_value(session, device, "subd", retry=200, valuef=subd)
            session.commit()
        version = 27
    if version < 30 <= target:  # version 29 → 30: add LoginSession.kind
        with Session(engine) as session:
            statement = update(db.LoginSession).values(kind=db.LoginSessionKind.COOKIE)
            session.exec(statement)
            session.commit()
        version = 30
    if version < 32 <= target:  # version 31 → 32: add 2 Device fields
        with Session(engine) as session:
            statement = update(db.Device).values(
                last_endpoint=None,
                last_handshake=None,
            )
            session.exec(statement)
            session.commit()
        version = 32
    if version < 33 <= target:  # version 32 → 33: update Device table
        with Session(engine) as session:
            statement = update(db.Device).values(min_test_level=db.TestLevel.CAN_UPDATE)
            session.exec(statement)
            session.commit()
        version = 33
    if version < 36 <= target:  # version 35 → 36: add long-poll timeout tuning fields
        with Session(engine) as session:
            statement = update(db.Device).values(
                long_poll_safe=25,  # see tag_long_poll_min_time
                long_poll_probe=None,
                long_polls=0,
            )
            session.exec(statement)
            session.commit()
        version = 36
    if version < 43 <= target:  # version 42 → 43: add Intf.name
        with Session(engine) as session:
            statement = select(db.Intf, db.Device.subd).join(
                db.Device,
                db.Intf.device_id == db.Device.id,
            )
            for intf, subd in session.exec(statement):
                intf.name = f'{db.wgif_prefix}{subd}{intf.id}'
            session.commit()
        version = 43
    if version < 44 <= target:  # version 43 → 44: recalculate all Intf address fields
        import ipaddress
        import secrets

        with Session(engine) as session:
            intfs = {intf.id: intf for intf in session.exec(select(db.Intf))}
            # calculate each multi-peer network once, before updating its clients
            for intf in intfs.values():
                if intf.base_intf_id is not None:
                    continue
                random40 = f'{(h := secrets.token_hex(5))[:2]}:{h[2:6]}:{h[6:]}'
                if intf.id == db.hub_id:
                    intf.host_bits = 16
                    intf.ipv4_base = str(
                        ipaddress.ip_address('172.16.0.0') + conf.get('backend.hub_bloc') * 2**16
                    )
                    intf.ipv6_base = str(
                        ipaddress.ip_address(f'fc{random40}::0')
                        + conf.get('backend.hub_bloc') * 2**16
                    )
                else:
                    intf.host_bits = conf.get('backend.host_bits')
                    intf.ipv4_base = str(
                        ipaddress.ip_address('10.0.0.0')
                        + secrets.randbelow(2 ** (32 - 8 - intf.host_bits)) * 2**intf.host_bits
                    )
                    intf.ipv6_base = str(ipaddress.ip_address(f'fd{random40}::0'))
            # keep each client's host_id; only its network details change
            for intf in intfs.values():
                if intf.base_intf_id is None:
                    continue
                base_intf = intfs[intf.base_intf_id]
                intf.ipv4_base = base_intf.ipv4_base
                intf.ipv6_base = base_intf.ipv6_base
                intf.host_bits = base_intf.host_bits
            session.commit()
        version = 44
    if version < 46 <= target:  # version 45 → 46: add Device columns (before Device → Base rename)
        with Session(engine) as session:
            statement = update(db.Device).values(
                file_version=db.default_fv,
                update_to_basedver_id=None,
                update_deadline=DateTime(1970, 1, 1, tzinfo=TimeZone.utc),
            )
            session.exec(statement)
            session.commit()
            # Manually fix DB to match update_bbbased_version() that now strips signature whitespace
            #    bbadmin sqlite3 .config/bitburrow/data.sqlite <<'SQL'
            #    UPDATE basedver
            #    SET other = json_set(
            #        other,
            #        '$.signature',
            #        replace(replace(replace(replace(
            #            json_extract(other, '$.signature'),
            #            char(10), ''), char(13), ''), char(9), ''), ' ', '')
            #    )
            #    WHERE json_type(other, '$.signature') = 'text';
            #    SQL
        version = 46
    if version < 49 <= target:  # version 48 → 49: rename 'device' to 'base' globally
        # new approach for this section and future migrations:
        # db_path is a private copy of the STORED schema, before the generic rebuild;
        # rename tables/columns and transform stored data here so the generic name-based
        # copier can preserve it; add a column here first if a data UPDATE needs it;
        # the rebuild afterward creates the latest columns, indexes and constraints;
        with sqlite_conn(db_path) as conn:
            conn.execute('BEGIN IMMEDIATE;')  # include the schema changes in one transaction
            tables = {
                r[0] for r in conn.execute('''SELECT name FROM sqlite_master WHERE type='table';''')
            }
            columns = {r[1] for r in conn.execute('PRAGMA table_info(account);')}
            if 'parent_id' in columns:
                conn.execute('''UPDATE account SET parent_id=NULL
                    WHERE parent_id=0 AND NOT EXISTS (SELECT 1 FROM account WHERE id=0);''')
            for old_table, table in (('device', 'base'), ('devicetask', 'basetask')):
                if old_table in tables:
                    conn.execute(f'ALTER TABLE "{old_table}" RENAME TO "{table}";')
            for table in ('intf', 'basetask', 'telemetry'):
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}");')}
                if 'device_id' in columns:
                    conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN device_id TO base_id;')
            # Older legacy schemas may predate LoginSession.kind; their old section
            # initializes it after rebuilding.
            columns = {r[1] for r in conn.execute('PRAGMA table_info(loginsession);')}
            if 'kind' in columns:
                conn.execute('''UPDATE loginsession SET kind='BASE_OTT' WHERE kind='DEVICE_OTT';''')
        version = 49
    # if version < ... <= target:
    #     ...
    #     version = ...


def migrate(db_path: str) -> None:
    if not os.path.exists(db_path):
        return  # not an error because it may have not been created yet
    db_path = os.path.abspath(db_path)  # temporary files and backups must share this directory
    with sqlite_conn(db_path) as conn:
        row = conn.execute('PRAGMA user_version;').fetchone()
        version = int(row[0] if row and row[0] is not None else 0)
    if version >= db_schema_version:
        return
    legacy_version = 48  # last version whose sections run *after* rebuilding
    logger.info(f"B49255 migrating database from version {version} to {db_schema_version}")
    with temporary_db(db_path) as working_path, temporary_db(db_path) as rebuilt_path:
        with sqlite_conn(db_path) as source, sqlite_conn(working_path) as working:
            source.backup(working)  # include committed WAL data in the snapshot
            working.execute('PRAGMA journal_mode=DELETE;')  # keep the snapshot in one file
        engine = create_engine(f'sqlite:///{working_path}')
        try:
            migrate_versions(working_path, max(version, legacy_version), db_schema_version, engine)
        finally:
            engine.dispose()
        engine = create_engine(f'sqlite:///{rebuilt_path}')
        try:
            SQLModel.metadata.create_all(engine)
            copy_data_from_old_db_to_new(rebuilt_path, working_path)
            migrate_versions(rebuilt_path, version, legacy_version, engine)
        finally:
            engine.dispose()
        with sqlite_conn(rebuilt_path) as conn:
            invalid_fk = conn.execute('PRAGMA foreign_key_check;').fetchone()
            if invalid_fk is not None:
                raise util.Berror(
                    f"B12048 migrated database has an invalid foreign key: {invalid_fk}"
                )
            conn.execute(f'PRAGMA user_version = {int(db_schema_version)};')
        util.rotate_backups(db_path, rebuilt_path)
