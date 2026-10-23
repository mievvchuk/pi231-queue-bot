"""Admin guards handlers."""
from .common import (
    BaseMiddleware,
    FSMContext,
    Message,
    PermissionDenied,
    admins,
)


class AdminOnly(BaseMiddleware):
    """Перша лінія захисту. Сервіси додатково перевіряють роль самі."""

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        if not admins.is_admin(data["conn"], user_id):
            raise PermissionDenied("У вас немає прав адміністратора.")
        return await handler(event, data)



async def _is_admin_filter(event, conn) -> bool:
    user = getattr(event, "from_user", None)
    if not user:
        return False
    return admins.is_admin(conn, user.id)



async def admin_state_filter(message: Message, state: FSMContext) -> bool:
    current_state = await state.get_state()
    return bool(
        current_state
        and (
            current_state.startswith("AdminPanelState")
            or current_state.startswith("AdminOrderState")
            or current_state.startswith("AdminManageState")
            or current_state.startswith("AdminUserState")
            or current_state.startswith("Admin")
        )
    )

