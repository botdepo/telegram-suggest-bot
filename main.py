"""Бот-предложка для Telegram-канала.

Подписчики присылают боту посты (текст, фото, видео, голосовые), владелец
получает каждый с кнопками «Опубликовать» / «Отклонить», бот публикует
одобренное в канал от имени канала и сообщает автору, что решили.

Настраивать ничего не нужно: кто первым напишет боту /start — тот
владелец; канал бот запоминает сам, когда владелец добавит его туда
администратором.

Токен бот получает из переменной окружения BOT_TOKEN — на botdepo её
подставляет панель, локально задайте сами (см. README).
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message

import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Сколько непроверенных постов может висеть у одного человека — защита от спама.
MAX_PENDING_PER_USER = 5

dp = Dispatcher()


def channel_line() -> str:
    title = storage.get("channel_title")
    if title:
        return f"Канал: «{title}». Одобренные посты уходят туда."
    return (
        "Канал ещё не подключён. Добавьте меня в канал администратором "
        "с правом «Публикация сообщений» — я сам его запомню и напишу вам."
    )


@dp.message(CommandStart())
async def start(message: Message) -> None:
    if storage.claim_owner(message.from_user.id):
        await message.answer(
            "Вы владелец этого бота.\n\n"
            f"{channel_line()}\n\n"
            "Дальше дайте подписчикам ссылку на меня — например, в описании канала. "
            "Каждый присланный пост придёт вам с кнопками «Опубликовать» и «Отклонить».\n\n"
            "Можете прислать пост себе сами, чтобы проверить.\n"
            "/status — канал и сколько постов ждут проверки."
        )
        return
    await message.answer(
        "Привет! Пришлите сюда пост для канала: текст, фото, видео или голосовое. "
        "Админ посмотрит и, если подойдёт, опубликует."
    )


@dp.message(Command("status"))
async def status(message: Message) -> None:
    if message.from_user.id != storage.owner_id():
        return
    await message.answer(f"{channel_line()}\nЖдут проверки: {storage.pending_count()}.")


@dp.my_chat_member(F.chat.type == "channel")
async def added_to_channel(event: ChatMemberUpdated, bot: Bot) -> None:
    owner = storage.owner_id()
    # Канал запоминаем, только если в него добавил владелец, — иначе любой
    # мог бы перенаправить предложку в свой канал.
    if owner is None or event.from_user.id != owner:
        return
    member = event.new_chat_member
    if member.status == "administrator" and getattr(member, "can_post_messages", False):
        storage.put("channel_id", str(event.chat.id))
        storage.put("channel_title", event.chat.title or "без названия")
        await bot.send_message(owner, f"Подключил канал «{event.chat.title}». Одобренные посты буду публиковать туда.")
    elif member.status == "administrator":
        await bot.send_message(
            owner,
            f"Я админ в «{event.chat.title}», но без права «Публикация сообщений». "
            "Включите его в настройках администратора — тогда подключу канал.",
        )
    elif str(event.chat.id) == storage.get("channel_id"):
        storage.put("channel_id", "")
        storage.put("channel_title", "")
        await bot.send_message(owner, f"Меня убрали из админов «{event.chat.title}». Публиковать пока некуда.")


@dp.message(F.chat.type == "private")
async def suggest(message: Message, bot: Bot) -> None:
    owner = storage.owner_id()
    if owner is None:
        await message.answer("Бот ещё не настроен. Владелец должен первым написать /start.")
        return
    if message.text and message.text.startswith("/"):
        await message.answer("Не знаю такую команду. Просто пришлите пост.")
        return
    if storage.pending_count(message.from_user.id) >= MAX_PENDING_PER_USER:
        await message.answer("У вас уже несколько постов ждут проверки. Подождите решения по ним.")
        return

    post_id = storage.add_post(message.from_user.id, message.message_id)
    user = message.from_user
    author = f"@{user.username}" if user.username else user.full_name
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{post_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rej:{post_id}"),
    ]])
    await bot.send_message(owner, f"Новый пост №{post_id} от {author}:")
    await bot.copy_message(owner, message.chat.id, message.message_id, reply_markup=keyboard)
    await message.answer("Отправил на проверку. Напишу, когда админ решит.")


@dp.callback_query(F.data.regexp(r"^(pub|rej):\d+$"))
async def decide(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id != storage.owner_id():
        await callback.answer()
        return
    action, raw_id = callback.data.split(":")
    post = storage.get_post(int(raw_id))
    if post is None or post[2] != "pending":
        await callback.answer("Этот пост уже разобран")
        return
    user_id, message_id, _ = post

    if action == "pub":
        channel = storage.get("channel_id")
        if not channel:
            await callback.answer("Сначала добавьте меня в канал администратором", show_alert=True)
            return
        try:
            await bot.copy_message(int(channel), user_id, message_id)
        except TelegramAPIError as e:
            logging.warning("не удалось опубликовать пост %s: %s", raw_id, e)
            await callback.answer("Не получилось опубликовать: автор удалил пост или у меня нет прав в канале", show_alert=True)
            return
        storage.set_status(int(raw_id), "published")
        verdict, note = "✅ Опубликован", "Ваш пост опубликовали в канале. Спасибо!"
    else:
        storage.set_status(int(raw_id), "rejected")
        verdict, note = "❌ Отклонён", "Ваш пост не подошёл для канала. Спасибо, что прислали."

    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=verdict, callback_data="done"),
    ]]))
    await callback.answer(verdict)
    try:
        await bot.send_message(user_id, note)
    except TelegramAPIError:
        pass  # автор заблокировал бота — ничего страшного


@dp.callback_query(F.data == "done")
async def already_done(callback: CallbackQuery) -> None:
    await callback.answer("Этот пост уже разобран")


async def main() -> None:
    bot = Bot(token=os.environ["BOT_TOKEN"])
    me = await bot.get_me()
    logging.info("бот запущен: @%s", me.username)
    if storage.owner_id() is None:
        logging.info("владельца пока нет: напишите боту /start первым — станете владельцем")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
