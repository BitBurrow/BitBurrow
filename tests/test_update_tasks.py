from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, Session, create_engine, select

import hub.db as db
import hub.migrate_db as migrate_db

base_id = 2
installed_xver = '0toqvn0-gaaa'
first_xver = '0toqvn1-eaaa'
second_xver = '0toqvn2-gaaa'


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'data.sqlite'
    engine = create_engine(f'sqlite:///{path}')
    monkeypatch.setattr(db, 'engine', engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for cver, epoch, level in (
            (installed_xver[:7], 31, db.TestLevel.CAN_UPDATE),
            (first_xver[:7], 32, db.TestLevel.CAN_PING),
            (second_xver[:7], 32, db.TestLevel.UNTESTED),
        ):
            blob = db.Blob(path='base/tree.json', version=cver, content=b'{}')
            session.add(blob)
            session.flush()
            session.add(db.BasedVer(cver=cver, blob_id=blob.id, epoch=epoch, test_level_init=level))
        session.add(
            db.Base(
                id=base_id,
                subd='test',
                platform=db.Platform.INIT,
                file_xver=installed_xver,
                min_test_level=db.TestLevel.CAN_PING,
            )
        )
        session.commit()
    yield path
    engine.dispose()


def ping(xver):
    db.process_ping(base_id, '192.0.2.2', {'platform': 'init', 'file_xver': xver}, 'test')


def approve_second_release():
    with Session(db.engine) as session:
        release = session.exec(select(db.BasedVer).where(db.BasedVer.cver == second_xver[:7])).one()
        release.test_level_init = db.TestLevel.CAN_UPDATE
        session.commit()


def report_update(task_id, status):
    return db.finish_task_result(task_id, status, base_id=base_id, method='update')


def test_newly_approved_update_waits_for_matching_ping(database):
    ping(installed_xver)
    first = db.next_task(base_id)
    assert first.args == {'target_xver': first_xver}
    assert first.update_deadline - first.assigned_at == db.update_timeout
    assert report_update(first.id, db.BaseTaskStatus.DONE)
    ping(installed_xver)  # The open update_deadline prevents a duplicate even after DONE.
    with Session(db.engine) as session:
        assert len(session.exec(select(db.BaseTask)).all()) == 1
    approve_second_release()
    ping(installed_xver)
    ping('0toqvn1-ecaa')  # Same cver, different full xver.
    assert db.next_task(base_id, unstale=False) is None
    assert db.get_task(first.id).update_deadline == first.update_deadline.replace(tzinfo=None)
    other_id = db.enqueue_task(base_id, 'no_op')
    assert db.next_task(base_id, unstale=False).id == other_id
    ping(first_xver)
    ping(first_xver)
    assert db.get_task(first.id).update_deadline is None
    with Session(db.engine) as session:
        release = session.exec(select(db.BasedVer).where(db.BasedVer.cver == first_xver[:7])).one()
        assert (release.used_total, release.used_fails) == (1, 0)
        assert release.test_level_init == db.TestLevel.CAN_UPDATE
    assert db.next_task(base_id, unstale=False).args == {'target_xver': second_xver}


@pytest.mark.parametrize('resolution', ['reported_failure', 'late_result', 'late_ping'])
def test_update_failure_releases_next_task(database, resolution):
    ping(installed_xver)
    first = db.next_task(base_id)
    approve_second_release()
    ping(installed_xver)
    if resolution == 'reported_failure':
        assert report_update(first.id, db.BaseTaskStatus.FAILED)
    else:
        assert report_update(first.id, db.BaseTaskStatus.DONE)
        with Session(db.engine) as session:
            session.get(db.BaseTask, first.id).update_deadline = datetime.now(timezone.utc)
            session.commit()
        if resolution == 'late_result':
            assert not report_update(first.id, db.BaseTaskStatus.DONE)
        else:
            ping(first_xver)
    second = db.next_task(base_id, unstale=False)
    assert second.args == {'target_xver': second_xver}
    assert not report_update(first.id, db.BaseTaskStatus.DONE)
    ping(first_xver)
    assert db.get_task(first.id).status == db.BaseTaskStatus.FAILED
    assert db.get_task(first.id).update_deadline is None
    assert db.get_task(second.id).update_deadline is not None
    with Session(db.engine) as session:
        release = session.exec(select(db.BasedVer).where(db.BasedVer.cver == first_xver[:7])).one()
        assert (release.used_total, release.used_fails) == (1, 1)
        assert release.test_level_init == db.TestLevel.CAN_PING


def poll_after_barrier(ready):
    ready.wait(timeout=5)
    return db.next_task(base_id, unstale=False)


def test_concurrent_polls_assign_one_update(database):
    approve_second_release()
    first_id = db.enqueue_task(base_id, 'update', args={'target_xver': first_xver})
    second_id = db.enqueue_task(base_id, 'update', args={'target_xver': second_xver})
    ready = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as workers:
        polls = [workers.submit(poll_after_barrier, ready) for _ in range(2)]
        assigned = [task.id for poll in polls if (task := poll.result()) is not None]
    assert assigned == [first_id]
    with Session(db.engine) as session:
        second = session.get(db.BaseTask, second_id)
        assert second.status == db.BaseTaskStatus.QUEUED
        second.update_deadline = datetime.now(timezone.utc) + db.update_timeout
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize('prior_status', [db.BaseTaskStatus.QUEUED, db.BaseTaskStatus.DONE])
def test_migration_preserves_pending_update(database, prior_status):
    deadline = datetime.now(timezone.utc).replace(tzinfo=None) + db.update_timeout
    ping(installed_xver)
    pending_id = db.next_task(base_id).id
    unrelated_id = db.enqueue_task(base_id, 'no_op')
    superseded_id = db.enqueue_task(base_id, 'update', args={'target_xver': second_xver})
    # Restore the v54 columns and simulate two assignments with only one tracked on the base.
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP INDEX ix_base_task_pending_update')
        conn.exec_driver_sql('ALTER TABLE basetask DROP COLUMN update_deadline')
        conn.exec_driver_sql(
            "ALTER TABLE base ADD COLUMN next_file_xver VARCHAR NOT NULL DEFAULT ''"
        )
        conn.exec_driver_sql('ALTER TABLE base ADD COLUMN update_deadline DATETIME')
        conn.exec_driver_sql(
            'UPDATE base SET next_file_xver=?, update_deadline=? WHERE id=?',
            (first_xver, deadline.isoformat(sep=' '), base_id),
        )
        conn.exec_driver_sql(
            'UPDATE basetask SET status=? WHERE id=?', (prior_status.name, pending_id)
        )
        conn.exec_driver_sql(
            "UPDATE basetask SET status='ASSIGNED', assigned_at=CURRENT_TIMESTAMP WHERE id=?",
            (superseded_id,),
        )
        conn.exec_driver_sql('PRAGMA user_version=54')
    db.engine.dispose()
    migrate_db.migrate(str(database))
    with db.engine.connect() as conn:
        version = conn.exec_driver_sql('PRAGMA user_version').scalar_one()
        assert version == migrate_db.db_schema_version
        columns = {row[1] for row in conn.exec_driver_sql('PRAGMA table_info(base)')}
        assert {'next_file_xver', 'update_deadline'}.isdisjoint(columns)
    assert db.get_task(unrelated_id).update_deadline is None
    assert db.get_task(superseded_id).status == db.BaseTaskStatus.FAILED
    assert db.get_task(pending_id).update_deadline == deadline
    if prior_status == db.BaseTaskStatus.QUEUED:
        redelivered = db.next_task(base_id, unstale=False)
        assert redelivered.id == pending_id
        assert redelivered.update_deadline == deadline
    ping(first_xver)
    assert db.get_task(pending_id).status == db.BaseTaskStatus.DONE
    assert db.get_task(pending_id).update_deadline is None
    assert not report_update(pending_id, db.BaseTaskStatus.DONE)
