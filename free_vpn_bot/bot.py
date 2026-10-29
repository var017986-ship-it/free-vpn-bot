from __future__ import annotations

import asyncio
import html
import json
import logging
import shlex
import subprocess
import time
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Settings, get_settings
from .database import AlreadyActivated, Database, DatabaseError, NotVerified, UnknownUser
from .plans import PLANS, Plan
from .xray import profile_uri

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("free_vpn_bot")


class BroadcastForm(StatesGroup):
    text = State()
    button_choice = State()
    button_label = State()
    button_url = State()
    confirm = State()


class BlockForm(StatesGroup):
    telegram_id = State()


@dataclass
class App:
    settings: Settings
    database: Database

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.settings.admin_ids

    def sync_profiles(self) -> None:
        result = subprocess.run(shlex.split(self.settings.sync_command), text=True, capture_output=True, timeout=90, check=False)
        if result.returncode:
            logger.error("Xray sync failed: %s", result.stderr[-300:])
            raise RuntimeError("Не удалось применить конфиги на VPN-сервере")

    def sync_mtg(self) -> None:
        secrets = self.database.active_mtg_secrets(self.settings.mtg_domain)
        payload = json.dumps({"secrets": [{"user": str(user_id), "secret": secret} for user_id, secret in secrets]})
        result = subprocess.run(
            shlex.split(self.settings.mtg_sync_command), input=payload, text=True,
            capture_output=True, timeout=90, check=False,
        )
        if result.returncode:
            logger.error("FakeTLS proxy sync failed: %s", result.stderr[-300:])
            raise RuntimeError("Не удалось обновить FakeTLS-прокси")

def human_bytes(value: int) -> str:
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} КБ"
    return f"{value / (1024 * 1024):.1f} МБ" if value < 1024 ** 3 else f"{value / (1024 ** 3):.2f} ГБ"


def human_left(expires_at: int) -> str:
    seconds = max(0, expires_at - int(time.time()))
    days, rest = divmod(seconds, 86_400)
    hours = rest // 3600
    return f"{days} дн. {hours} ч."


def main_menu(settings: Settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Бесплатный VPN", callback_data="plan:free")],
        [InlineKeyboardButton(text="👨‍👩‍👧‍👦 Семейный VPN", callback_data="plan:family")],
        [InlineKeyboardButton(text="🔗 Моя подписка", callback_data="menu:configs")],
        [InlineKeyboardButton(text="✈️ Получить прокси Telegram", callback_data="menu:proxy")],
        [InlineKeyboardButton(text="🛡️ Telegram FakeTLS", callback_data="menu:mtg")],
        [InlineKeyboardButton(text="ℹ️ Подключение", callback_data="menu:guide")],
        [InlineKeyboardButton(text="🆘 Поддержка", url=settings.support_url)],
    ])


def captcha_markup(challenge: dict) -> InlineKeyboardMarkup:
    rows = []
    for choice in challenge["choices"]:
        rows.append([InlineKeyboardButton(text=str(choice), callback_data=f"captcha:{challenge['id']}:{choice}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_url(settings: Settings, token: str) -> str:
    return f"{settings.subscription_base_url}/s/{token}"


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="📋 Тарифы", callback_data="admin:tariffs")],
        [InlineKeyboardButton(text="🚫 Заблокировать пользователя", callback_data="admin:block")],
    ])


async def show_menu(message: Message, app: App, text: str = "Выберите действие:") -> None:
    await message.answer(text, reply_markup=main_menu(app.settings))


def make_router(app: App) -> Router:
    router = Router()

    async def show_captcha(message: Message) -> None:
        challenge = await asyncio.to_thread(app.database.issue_captcha, message.from_user.id)
        await message.answer(
            "Для получения бесплатного доступа пройдите быструю проверку.\n"
            f"Решите пример: <b>{challenge['question']}</b>",
            reply_markup=captcha_markup(challenge),
        )

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if not message.from_user:
            return
        if not await asyncio.to_thread(app.database.is_verified, message.from_user.id):
            await show_captcha(message)
            return
        await show_menu(message, app, "С возвращением. Выберите действие:")

    @router.message(Command("cancel"), StateFilter("*"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Действие отменено.")

    @router.callback_query(F.data.startswith("captcha:"))
    async def captcha(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            return
        try:
            _, challenge_id, answer = callback.data.split(":", 2)
            passed = await asyncio.to_thread(
                app.database.pass_captcha, callback.from_user.id, challenge_id, int(answer),
                callback.from_user.username, callback.from_user.first_name,
            )
        except (ValueError, DatabaseError):
            passed = False
        if not passed:
            await callback.answer("Ответ неверный или время проверки истекло.", show_alert=True)
            challenge = await asyncio.to_thread(app.database.issue_captcha, callback.from_user.id)
            await callback.message.edit_text(
                "Попробуйте ещё раз. Решите пример: "
                f"<b>{challenge['question']}</b>",
                reply_markup=captcha_markup(challenge),
            )
            return
        await callback.answer("Проверка пройдена")
        await show_menu(callback.message, app, "✅ Проверка пройдена. Теперь доступна выдача конфигов.")

    @router.callback_query(F.data.startswith("plan:"))
    async def activate_plan(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message or not callback.data:
            return
        plan_code = callback.data.removeprefix("plan:")
        plan = PLANS.get(plan_code)
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return
        try:
            activation, profiles = await asyncio.to_thread(app.database.activate, callback.from_user.id, plan_code)
            try:
                await asyncio.to_thread(app.sync_profiles)
            except Exception:
                await asyncio.to_thread(app.database.discard_activation, activation["id"])
                try:
                    await asyncio.to_thread(app.sync_profiles)
                except Exception:
                    logger.exception("Rollback Xray sync failed")
                raise
        except AlreadyActivated as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except (NotVerified, RuntimeError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.answer("Тариф активирован")
        await callback.message.answer(
            f"✅ <b>{plan.title}</b> активирован.\n"
            f"Срок: {plan.days} дней\nТрафик: {plan.quota_bytes // (1024 ** 3)} ГБ\n"
            f"Устройств: до {plan.slots}\n\n"
            "Добавьте ссылку подписки в Happ — все конфиги появятся и будут обновляться автоматически."
        )
        url = subscription_url(app.settings, activation["subscription_token"])
        await callback.message.answer(
            f"<b>Ссылка подписки</b>\n<code>{html.escape(url)}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 Открыть подписку", url=url),
            ]]),
        )
        await callback.message.answer("VLESS-конфиг «Финляндия» для ручного подключения:")
        await send_profiles(callback.message, app, profiles)

    async def send_profiles(message: Message, app: App, profiles: list[dict]) -> None:
        for profile in profiles:
            uri = profile_uri(app.settings, profile["id"], profile["label"])
            await message.answer(f"<b>{html.escape(profile['label'])}</b>\n<code>{html.escape(uri)}</code>")

    @router.callback_query(F.data == "menu:configs")
    async def configs(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message:
            return
        activations = await asyncio.to_thread(app.database.user_activations, callback.from_user.id)
        if not activations:
            await callback.answer()
            await callback.message.answer("У вас пока нет активированных тарифов.", reply_markup=main_menu(app.settings))
            return
        await callback.answer()
        for activation in activations:
            plan = PLANS.get(activation["plan_code"])
            title = plan.title if plan else activation["plan_code"]
            remaining = max(0, activation["quota_bytes"] - activation["used_bytes"])
            await callback.message.answer(
                f"<b>{html.escape(title)}</b> — {html.escape(activation['status'])}\n"
                f"Осталось: {human_left(activation['expires_at'])}\n"
                f"Трафик: {human_bytes(activation['used_bytes'])} из {human_bytes(activation['quota_bytes'])}\n"
                f"Устройства: занято {activation['device_count']} из {plan.slots if plan else 1}\n"
                f"Осталось мест: {max(0, (plan.slots if plan else 1) - activation['device_count'])}"
            )
            if activation["status"] == "active":
                url = subscription_url(app.settings, activation["subscription_token"])
                await callback.message.answer(
                    f"<b>Ссылка подписки</b>\n<code>{html.escape(url)}</code>",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🚀 Открыть подписку", url=url),
                    ]]),
                )
                await send_profiles(callback.message, app, activation["profiles"])

    @router.callback_query(F.data == "menu:guide")
    async def guide(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.answer()
        await callback.message.answer(
            "<b>Как подключиться</b>\n\n"
            "1. Нажмите «Моя подписка».\n"
            "2. Скопируйте HTTPS-ссылку подписки.\n"
            "3. В Happ нажмите «+» → добавить подписку из буфера/URL.\n"
            "4. Обновите подписку и включите нужный сервер.\n\n"
            "VLESS-ссылки ниже подходят для ручного импорта одного устройства."
        )

    @router.callback_query(F.data == "menu:proxy")
    async def mtproto_proxy(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message:
            return
        if not app.settings.mtproto_host:
            await callback.answer("Прокси временно недоступен", show_alert=True)
            return
        if not await asyncio.to_thread(app.database.is_verified, callback.from_user.id):
            await callback.answer("Сначала пройдите проверку", show_alert=True)
            return
        if not app.settings.mtproto_secret:
            await callback.answer("Прокси временно недоступен", show_alert=True)
            return
        url = (
            "https://t.me/proxy?server="
            f"{app.settings.mtproto_host}&port={app.settings.mtproto_port}&secret={app.settings.mtproto_secret}"
        )
        await callback.answer()
        await callback.message.answer(
            "<b>Ваш Telegram-прокси</b>\n\n"
            "Нажмите кнопку ниже, чтобы добавить прокси в Telegram.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✈️ Подключить прокси", url=url),
            ]]),
        )

    @router.callback_query(F.data == "menu:mtg")
    async def mtg_proxy(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message:
            return
        if not app.settings.mtg_host or not app.settings.mtg_sync_command:
            await callback.answer("FakeTLS-прокси временно недоступен", show_alert=True)
            return
        if not await asyncio.to_thread(app.database.is_verified, callback.from_user.id):
            await callback.answer("Сначала пройдите проверку", show_alert=True)
            return
        try:
            await asyncio.to_thread(app.sync_mtg)
        except RuntimeError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        secret = await asyncio.to_thread(
            app.database.mtg_secret, callback.from_user.id, app.settings.mtg_domain
        )
        url = f"https://t.me/proxy?server={app.settings.mtg_host}&port={app.settings.mtg_port}&secret={secret}"
        await callback.answer()
        await callback.message.answer(
            "<b>Персональный Telegram FakeTLS-прокси</b>\n\n"
            "Если подключение долго проверяется, используйте обычную кнопку прокси как резерв.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🛡️ Подключить FakeTLS", url=url),
            ]]),
        )

    @router.message(Command("admin"))
    async def admin(message: Message) -> None:
        if not message.from_user or not app.is_admin(message.from_user.id):
            return
        await message.answer("Админ-панель:", reply_markup=admin_menu())

    @router.callback_query(F.data == "admin:stats")
    async def stats(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message or not app.is_admin(callback.from_user.id):
            return
        data = await asyncio.to_thread(app.database.statistics)
        await callback.answer()
        await callback.message.answer(
            "<b>Статистика</b>\n"
            f"Проверенных пользователей: {data['verified_users']}\n"
            f"Заблокировано: {data['blocked_users']}\n"
            f"Активно бесплатных: {data['active_free']}\n"
            f"Активно семейных: {data['active_family']}\n"
            f"Учтено трафика: {human_bytes(data['traffic_bytes'])}"
        )

    @router.callback_query(F.data == "admin:tariffs")
    async def tariffs(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.message or not app.is_admin(callback.from_user.id):
            return
        rows = await asyncio.to_thread(app.database.tariff_summary)
        await callback.answer()
        text = "<b>Тарифы</b>\n" + ("\n".join(
            f"{html.escape(PLANS.get(row['plan_code'], Plan(row['plan_code'], row['plan_code'], 0, 0, 0)).title)} — "
            f"{html.escape(row['status'])}: {row['count']}" for row in rows
        ) if rows else "Пока нет активаций.")
        await callback.message.answer(text)

    @router.callback_query(F.data == "admin:block")
    async def block_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not app.is_admin(callback.from_user.id):
            return
        await state.set_state(BlockForm.telegram_id)
        await callback.answer()
        await callback.message.answer("Отправьте числовой Telegram ID пользователя для блокировки. /cancel — отмена.")

    @router.message(BlockForm.telegram_id)
    async def block_finish(message: Message, state: FSMContext) -> None:
        if not message.from_user or not app.is_admin(message.from_user.id):
            return
        try:
            target = int((message.text or "").strip())
            revoked = await asyncio.to_thread(app.database.revoke_user, target)
            await asyncio.to_thread(app.sync_profiles)
            if app.settings.mtg_sync_command:
                await asyncio.to_thread(app.sync_mtg)
        except (ValueError, UnknownUser, RuntimeError) as exc:
            await message.answer(f"Не удалось заблокировать: {html.escape(str(exc))}")
            return
        await state.clear()
        await message.answer(f"Пользователь заблокирован. Отозвано тарифов: {revoked}.")

    @router.callback_query(F.data == "admin:broadcast")
    async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not app.is_admin(callback.from_user.id):
            return
        await state.set_state(BroadcastForm.text)
        await callback.answer()
        await callback.message.answer("Отправьте текст рассылки. Форматирование не применяется. /cancel — отмена.")

    @router.message(BroadcastForm.text)
    async def broadcast_text(message: Message, state: FSMContext) -> None:
        if not message.from_user or not app.is_admin(message.from_user.id) or not message.text:
            return
        await state.update_data(text=message.text[:3500])
        await state.set_state(BroadcastForm.button_choice)
        await message.answer("Добавить кнопку к рассылке?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Да", callback_data="broadcast:button:yes"),
            InlineKeyboardButton(text="Нет", callback_data="broadcast:button:no"),
        ]]))

    @router.callback_query(F.data.startswith("broadcast:button:"), BroadcastForm.button_choice)
    async def broadcast_button_choice(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not callback.message or not app.is_admin(callback.from_user.id):
            return
        yes = callback.data == "broadcast:button:yes"
        await callback.answer()
        if yes:
            await state.set_state(BroadcastForm.button_label)
            await callback.message.answer("Напишите название кнопки.")
        else:
            await show_broadcast_preview(callback.message, state)

    @router.message(BroadcastForm.button_label)
    async def broadcast_label(message: Message, state: FSMContext) -> None:
        if not message.from_user or not app.is_admin(message.from_user.id) or not message.text:
            return
        await state.update_data(button_label=message.text[:64])
        await state.set_state(BroadcastForm.button_url)
        await message.answer("Теперь отправьте ссылку кнопки, начиная с https://.")

    @router.message(BroadcastForm.button_url)
    async def broadcast_url(message: Message, state: FSMContext) -> None:
        if not message.from_user or not app.is_admin(message.from_user.id) or not message.text:
            return
        url = message.text.strip()
        if not url.startswith("https://"):
            await message.answer("Нужна ссылка, начинающаяся с https://.")
            return
        await state.update_data(button_url=url)
        await show_broadcast_preview(message, state)

    async def show_broadcast_preview(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        button = None
        if data.get("button_label"):
            button = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=data["button_label"], url=data["button_url"])
            ]])
        await message.answer("<b>Предпросмотр рассылки</b>\n\n" + html.escape(data["text"]), reply_markup=button)
        await state.set_state(BroadcastForm.confirm)
        await message.answer("Отправить это сообщение?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast:send"),
            InlineKeyboardButton(text="Отмена", callback_data="broadcast:cancel"),
        ]]))

    @router.callback_query(F.data == "broadcast:cancel", BroadcastForm.confirm)
    async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer("Отменено")

    @router.callback_query(F.data == "broadcast:send", BroadcastForm.confirm)
    async def broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if not callback.from_user or not callback.message or not app.is_admin(callback.from_user.id):
            return
        data = await state.get_data()
        button = None
        if data.get("button_label"):
            button = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=data["button_label"], url=data["button_url"])
            ]])
        sent = failed = 0
        for target in await asyncio.to_thread(app.database.broadcast_targets):
            try:
                await bot.send_message(target, html.escape(data["text"]), reply_markup=button)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await state.clear()
        await callback.answer("Рассылка завершена")
        await callback.message.answer(f"Рассылка завершена: доставлено {sent}, недоступно {failed}.")

    return router


async def run() -> None:
    settings = get_settings()
    if not settings.is_complete_for_bot:
        raise RuntimeError("Не заданы обязательные параметры бота")
    database = Database(settings.database_path, settings.captcha_secret)
    app = App(settings, database)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(make_router(app))
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
