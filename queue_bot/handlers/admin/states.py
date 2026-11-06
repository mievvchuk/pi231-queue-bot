from aiogram.fsm.state import State, StatesGroup


class AdminPanelState(StatesGroup):
    in_main_menu = State()
    in_queues_menu = State()
    in_students_menu = State()
    in_reports_menu = State()
    in_settings_menu = State()
    in_schedule_menu = State()
    select_queue_for_view = State()
    select_queue_for_add_student = State()
    select_queue_for_order = State()
    select_queue_for_close = State()
    select_subject_for_report = State()
    add_student_names = State()
    confirm_finish_queue = State()



class AdminUserState(StatesGroup):
    in_users_menu = State()
    browsing_users = State()
    viewing_card = State()
    adding_user = State()
    searching_user = State()
    editing_name = State()
    confirming_name_edit = State()
    confirming_delete = State()



class AdminSubjectsState(StatesGroup):
    in_menu = State()
    select_subject_for_teacher = State()
    entering_teacher_name = State()
    entering_new_subject_name = State()
    entering_new_subject_teacher = State()
    select_subject_for_rename = State()
    entering_subject_new_name = State()
    select_subject_for_remove_teacher = State()
    select_teacher_to_remove = State()
    select_subject_to_delete = State()
    confirming_delete_subject = State()



class AdminOrderState(StatesGroup):
    picking_student = State()
    managing_entry = State()
    entering_number = State()
    entering_name = State()



class AdminManageState(StatesGroup):
    in_admins_menu = State()
    viewing_admins = State()
    add_admin_id = State()
    select_admin_to_remove = State()
    entering_broadcast = State()
    confirming_broadcast = State()
    entering_maintenance_time = State()

