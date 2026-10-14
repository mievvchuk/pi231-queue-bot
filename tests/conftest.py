import pytest

import db
from services import admins, users


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    admins.init_admins(connection, [100])
    yield connection
    connection.close()


@pytest.fixture
def make_user(conn):
    def _make(telegram_id: int, name: str):
        users.register_user(conn, telegram_id, name)
    return _make
