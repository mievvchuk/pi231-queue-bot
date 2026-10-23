from services import queues, users, replacements


def test_consultation_renewal_preserves_type_with_parallel_lesson(conn):
    lesson = queues.create_session(conn, 100, 1, 1, queue_type='lesson')
    consultation = queues.create_session(conn, 100, 1, 1, queue_type='consultation')
    renewed = queues.finish_session(conn, 100, consultation, create_new=True)
    assert queues.get_session(conn, renewed)['queue_type'] == 'consultation'
    assert queues.get_session(conn, lesson)['status'] == 'open'


def test_repeatable_swap_remains_available(conn, make_user):
    sid = queues.create_session(conn, 100, 1, 1)
    make_user(1, 'Петренко Іван')
    make_user(2, 'Коваль Андрій')
    queues.join_queue(conn, 1, sid)
    queues.join_queue(conn, 2, sid)
    a, b = queues.get_entries(conn, sid)
    queues.swap_entries(conn, a['id'], b['id'])
    queues.swap_entries(conn, a['id'], b['id'])
    assert queues.get_user_position(conn, 1, sid) == 1


def test_latest_replacement_still_archives_earlier_package(conn):
    for day in ['2026-10-26', '2026-10-27']:
        replacements.save_replacement(conn, {'date': day, 'lesson_number': 1, 'new_subject': 'Заміна'})
    assert replacements.get_replacements(conn, date='2026-10-26') == []
    assert len(replacements.get_replacements(conn, date='2026-10-26', include_archived=True)) == 1


def test_database_url_has_no_embedded_default(monkeypatch):
    import importlib
    import config
    monkeypatch.delenv('DATABASE_URL', raising=False)
    importlib.reload(config)
    has_default = bool(config.DATABASE_URL)
    assert not has_default


def test_startup_rejects_missing_database_before_health_server(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import bot
    monkeypatch.setattr(bot.config, 'DATABASE_URL', '')
    server = AsyncMock()
    monkeypatch.setattr(bot, 'run_web_server', server)
    import pytest
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        asyncio.run(bot.main())
    server.assert_not_awaited()
