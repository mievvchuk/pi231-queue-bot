import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, Message, User, Chat
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from queue_bot import config
from queue_bot.services import subjects as subjects_module
from queue_bot.services import admins
from queue_bot.services.errors import BotError, PermissionDenied


@pytest.fixture
def sample_subjects():
    return [
        {
            "id": 1,
            "name": "Програмування в Інтернет",
            "teachers": [
                {"id": 1, "name": "Когут В.С."},
                {"id": 4, "name": "Барабаш Д.С."},
            ],
        },
        {
            "id": 2,
            "name": "Людино-машинна взаємодія",
            "teachers": [
                {"id": 2, "name": "Кондратюк Є.С."},
            ],
        },
    ]


def test_resolve_or_create_teacher_reuses_id(sample_subjects):
    # Existing teacher: reuses ID 4
    t = subjects_module.resolve_or_create_teacher(sample_subjects, "барабаш д.с.")
    assert t["id"] == 4
    assert t["name"] == "Барабаш Д.С."

    # New teacher: gets max_id + 1
    t_new = subjects_module.resolve_or_create_teacher(sample_subjects, "Новий В.В.")
    assert t_new["id"] == 5
    assert t_new["name"] == "Новий В.В."


def test_add_teacher_to_subject(conn, sample_subjects):
    # Attach existing teacher to subject 2
    t = subjects_module.add_teacher_to_subject(conn, sample_subjects, 2, "Когут В.С.")
    assert t["id"] == 1
    sub2 = subjects_module.find_subject(sample_subjects, 2)
    assert len(sub2["teachers"]) == 2
    assert any(x["id"] == 1 for x in sub2["teachers"])

    # Duplicate attachment rejected
    with pytest.raises(BotError, match="вже прикріплений"):
        subjects_module.add_teacher_to_subject(conn, sample_subjects, 2, "Когут В.С.")


def test_add_new_subject(conn, sample_subjects):
    new_sub = subjects_module.add_new_subject(conn, sample_subjects, "Теорія алгоритмів", "Іванов І.І.")
    assert new_sub["id"] == 3
    assert new_sub["name"] == "Теорія алгоритмів"
    assert len(sample_subjects) == 3

    # Duplicate subject name rejected
    with pytest.raises(BotError, match="вже існує"):
        subjects_module.add_new_subject(conn, sample_subjects, "теорія алгоритмів", "Петров П.П.")


def test_rename_and_delete_subject(conn, sample_subjects):
    # Rename
    sub = subjects_module.rename_subject(conn, sample_subjects, 1, "Веб-програмування")
    assert sub["name"] == "Веб-програмування"

    # Remove teacher
    subjects_module.remove_teacher_from_subject(conn, sample_subjects, 1, 4)
    sub1 = subjects_module.find_subject(sample_subjects, 1)
    assert len(sub1["teachers"]) == 1

    # Cannot remove last teacher
    with pytest.raises(BotError, match="щонайменше один викладач"):
        subjects_module.remove_teacher_from_subject(conn, sample_subjects, 1, 1)

    # Delete subject
    subjects_module.delete_subject(conn, sample_subjects, 2)
    assert len(sample_subjects) == 1
    assert sample_subjects[0]["id"] == 1

    # Cannot delete last subject in bot
    with pytest.raises(BotError, match="останній предмет"):
        subjects_module.delete_subject(conn, sample_subjects, 1)


def test_student_proposals_persistence_and_approval(conn, sample_subjects):
    # 1. Student creates request to attach teacher
    req = subjects_module.create_subject_request(
        conn,
        user_telegram_id=999,
        user_name="Студент Тест",
        req_type="add_teacher",
        subject_id=2,
        subject_name="Людино-машинна взаємодія",
        teacher_name="Когут В.С.",
    )
    req_id = req["id"]
    loaded = subjects_module.get_subject_request(conn, req_id)
    assert loaded is not None
    assert loaded["status"] == "pending"

    # 2. Simulate owner approval
    subjects_module.add_teacher_to_subject(conn, sample_subjects, loaded["subject_id"], loaded["teacher_name"])
    subjects_module.update_subject_request_status(conn, req_id, "approved")

    updated = subjects_module.get_subject_request(conn, req_id)
    assert updated["status"] == "approved"

    sub2 = subjects_module.find_subject(sample_subjects, 2)
    assert any(t["name"] == "Когут В.С." for t in sub2["teachers"])


@pytest.mark.asyncio
async def test_owner_only_restrictions_on_subjects(conn, sample_subjects):
    from queue_bot.handlers.admin import admin_rename_subject_start, admin_delete_subject_start

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=200, user_id=200))

    # User 200 is an admin, but NOT owner
    admins.add_admin(conn, 100, 200)
    assert admins.is_admin(conn, 200)
    assert not admins.is_owner(conn, 200)

    msg = MagicMock()
    msg.from_user.id = 200
    msg.answer = AsyncMock()

    # Non-owner cannot rename or delete
    with pytest.raises(PermissionDenied):
        await admin_rename_subject_start(msg, state, conn, sample_subjects)

    with pytest.raises(PermissionDenied):
        await admin_delete_subject_start(msg, state, conn, sample_subjects)
