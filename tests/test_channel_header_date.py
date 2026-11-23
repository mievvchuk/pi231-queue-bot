from queue_bot.services.recognition import parse_date


def test_source_weekday_in_announcement_does_not_override_table_date():
    assert parse_date([
        'Заміни в розкладі 18 листопада 2026 р.',
        'Середа – чисельник',
        'ЧЕТВЕРТІ ПАРИ ПРОВОДЯТЬСЯ ЗА РОЗКЛАДОМ ВІВТОРКА ТРЕТЬОЇ ПАРИ ЧИСЕЛЬНИК',
    ]) == '2026-11-18'


def test_missing_weekday_is_not_inferred_from_source_day():
    assert parse_date(['Заміни в розкладі 18 листопада 2026 р.',
                       '4 пари за розкладом понеділка 3 пари']) == '2026-11-18'


def test_weekday_still_recovers_a_dropped_date_digit():
    assert parse_date(['Заміни в розкладі 4 листопада 2026 р.', 'Субота – чисельник']) == '2026-11-14'


def test_contradictory_target_weekday_remains_unresolved():
    assert parse_date(['Заміни в розкладі 18 листопада 2026 р.', 'Вівторок']) is None


def test_numeric_header_date_and_invalid_year():
    assert parse_date(['Заміни в розкладі 24.11.2026', 'Вівторок – знаменник']) == '2026-11-24'
    assert parse_date(['Заміни в розкладі 24 листопада 0000']) is None
