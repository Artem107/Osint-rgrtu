import asyncio
import os
import io
import aiosqlite
from openpyxl import Workbook
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram import BaseMiddleware
from aiogram.types import Message

# === Настройки ===
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = "users.db"
TASKS_DIR = "tasks"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ---- Ограничение частоты отправки сообщений пользователем ----
last_message_time: dict[int, datetime] = {}
COOLDOWN = timedelta(seconds=10)

# ---- Состояния ----
class Registration(StatesGroup):
    team_name = State()
    group_number = State()
    tg_link = State()

class SendMessage(StatesGroup):
    waiting_for_text = State()

class TaskUpload(StatesGroup):
    waiting_for_file = State()
    confirm_file = State()

class RegistrationMiddleware(BaseMiddleware):
    async def __call__(self, handler, update, data):
        # Если апдейт не содержит сообщения, пропускаем
        if not hasattr(update, "message") or update.message is None:
            return await handler(update, data)

        message: Message = update.message
        user_id = message.from_user.id

        # Админ и /start могут обходить проверку
        if user_id == ADMIN_ID or (message.text and message.text.startswith("/start")):
            return await handler(update, data)

        # Получаем состояние пользователя
        state: FSMContext = data.get("state")
        if state is not None:
            current_state = await state.get_state()
            # Если пользователь в процессе регистрации, пропускаем проверку
            if current_state is not None and current_state.startswith("Registration:"):
                return await handler(update, data)

        # Проверяем регистрацию
        if not await is_registered(user_id):
            await message.answer("❗ Вы не зарегистрированы. Используйте /start для регистрации.")
            return  # дальше хендлер не вызывается

        return await handler(update, data)

dp.update.middleware(RegistrationMiddleware())

# ---- Клавиатуры ----
def main_keyboard(user_id: int = 0):
    if user_id == ADMIN_ID:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📋 Все команды")],
                [KeyboardButton(text="📥 Выгрузить все ответы команд")],
                [KeyboardButton(text="📄 Подготовить задание")],
            ],
            resize_keyboard=True
        )
    else:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Участники утечки данных (имена) - 2 балла")],
                [KeyboardButton(text="Какие шифры использовались? - 3 балла")],
                [KeyboardButton(text="Домен - 1 балл")],
                [KeyboardButton(text="Книги (названия) - 3 балла")],
                [KeyboardButton(text="📋 Моя команда"), KeyboardButton(text="📄 Мои ответы")],
                [KeyboardButton(text="📘 Получить задание")],
            ],
            resize_keyboard=True
        )

def back_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅ Назад")]],
        resize_keyboard=True
    )

async def tasks_keyboard():
    files = [f for f in os.listdir(TASKS_DIR) if f.endswith(".pdf")]
    if not files:
        return None
    buttons = [[KeyboardButton(text=f)] for f in files]
    buttons.append([KeyboardButton(text="⬅ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def confirm_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]
        ],
        resize_keyboard=True
    )

async def teams_keyboard():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT team_name FROM users ORDER BY team_name")
        rows = await cursor.fetchall()
    if not rows:
        return None
    buttons = [[KeyboardButton(text=team_name)] for (team_name,) in rows]
    buttons.append([KeyboardButton(text="⬅ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ---- Инициализация базы ----
async def init_db():
    os.makedirs(TASKS_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                team_name TEXT,
                group_number TEXT,
                username TEXT,
                tg_link TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                category TEXT,
                answer TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()

# ---- Проверка регистрации ----
async def is_registered(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone() is not None

# ---- Получение имени команды ----
async def get_team_name(user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT team_name FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

# ---- /start ----
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👑 Привет, администратор!", reply_markup=main_keyboard(ADMIN_ID))
        return
    if await is_registered(message.from_user.id):
        await message.answer("Вы уже зарегистрированы ✅", reply_markup=main_keyboard(message.from_user.id))
    else:
        await message.answer("Введите название вашей команды:")
        await state.set_state(Registration.team_name)

# ---- Регистрация ----
@dp.message(StateFilter(Registration.team_name))
async def reg_team_name(message: types.Message, state: FSMContext):
    await state.update_data(team_name=message.text)
    await message.answer("Введите номер вашей группы:")
    await state.set_state(Registration.group_number)

@dp.message(StateFilter(Registration.group_number))
async def reg_group_number(message: types.Message, state: FSMContext):
    await state.update_data(group_number=message.text)
    if not message.from_user.username:
        await message.answer("❗ У вас нет username в Telegram. Пожалуйста, отправьте ссылку на ваш аккаунт (например: https://t.me/username или https://t.me/+12345678912):")
        await state.set_state(Registration.tg_link)
    else:
        data = await state.get_data()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, team_name, group_number, username, tg_link) VALUES (?, ?, ?, ?, ?)",
                (message.from_user.id, data["team_name"], data["group_number"], message.from_user.username, f"https://t.me/{message.from_user.username}")
            )
            await db.commit()
        await message.answer("✅ Регистрация завершена!", reply_markup=main_keyboard(message.from_user.id))
        await state.clear()

@dp.message(StateFilter(Registration.tg_link))
async def reg_tg_link(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tg_link = message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, team_name, group_number, username, tg_link) VALUES (?, ?, ?, ?, ?)",
            (message.from_user.id, data["team_name"], data["group_number"], message.from_user.username, tg_link)
        )
        await db.commit()
    await message.answer("✅ Регистрация завершена!", reply_markup=main_keyboard(message.from_user.id))
    await state.clear()

# ---- Ввод ответов ----
@dp.message(F.text.in_([
    "Участники утечки данных (имена) - 2 балла",
    "Какие шифры использовались? - 3 балла",
    "Домен - 1 балл",
    "Книги (названия) - 3 балла"
]))
async def answer_input(message: types.Message, state: FSMContext):
    await state.update_data(category=message.text)
    await message.answer(f"Введите ответ по категории:\n<b>{message.text}</b>", parse_mode="HTML", reply_markup=back_keyboard())
    await state.set_state(SendMessage.waiting_for_text)

@dp.message(F.text == "⬅ Назад")
async def go_back(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🔙 Возврат в меню", reply_markup=main_keyboard(message.from_user.id))

# ---- Сохранение ответа с ограничением частоты ----
@dp.message(StateFilter(SendMessage.waiting_for_text))
async def save_answer(message: types.Message, state: FSMContext):
    now = datetime.utcnow()
    last_time = last_message_time.get(message.from_user.id)
    if last_time and now - last_time < COOLDOWN:
        remaining = int((COOLDOWN - (now - last_time)).total_seconds())
        await message.answer(f"❗ Подождите {remaining} секунд перед отправкой следующего ответа.", reply_markup=back_keyboard())
        return

    data = await state.get_data()
    category = data["category"]
    team_name = await get_team_name(message.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO answers (user_id, category, answer) VALUES (?, ?, ?)",
                         (message.from_user.id, category, message.text))
        await db.commit()

    await bot.send_message(ADMIN_ID, f"📩 Ответ от команды <b>{team_name}</b>\n\n🏷️ <b>{category}</b>\n💬 {message.text}", parse_mode="HTML")
    last_message_time[message.from_user.id] = now

    await message.answer("✅ Ответ отправлен!", reply_markup=main_keyboard(message.from_user.id))
    await state.clear()

# ---- Моя команда ----
@dp.message(F.text == "📋 Моя команда")
async def my_team(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT team_name, group_number FROM users WHERE user_id = ?", (message.from_user.id,))
        row = await cursor.fetchone()
    if row:
        team_name, group_number = row
        await message.answer(f"🏷️ Команда: <b>{team_name}</b>\n🔢 Группа: <b>{group_number}</b>", parse_mode="HTML")
    else:
        await message.answer("❗ Вы не зарегистрированы. Используйте /start")

# ---- Мои ответы ----
@dp.message(F.text == "📄 Мои ответы")
async def my_answers(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT category, answer, created_at FROM answers WHERE user_id = ? ORDER BY created_at DESC", (message.from_user.id,))
        rows = await cursor.fetchall()
    if not rows:
        await message.answer("❗ У вас пока нет ответов.")
        return
    text = "📄 <b>Ваши ответы:</b>\n\n"
    for category, answer, created_at in rows:
        dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
        text += f"🏷️ <b>{category}</b>\n💬 {answer}\n⏰ {dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    await message.answer(text.strip(), parse_mode="HTML")

# ---- Выгрузить все ответы ----
@dp.message(F.text == "📥 Выгрузить все ответы команд")
async def export_answers(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Только для администратора.")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT u.team_name, u.group_number, a.category, a.answer, a.created_at
            FROM answers a
            JOIN users u ON a.user_id = u.user_id
            ORDER BY u.team_name, a.created_at
        """)
        rows = await cursor.fetchall()
    if not rows:
        return await message.answer("❗ Нет ответов для выгрузки.")
    wb = Workbook()
    ws = wb.active
    ws.title = "Ответы"
    ws.append(["Команда", "Группа", "Категория", "Ответ", "Время (МСК)"])
    for team, group, category, answer, created_at in rows:
        dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
        ws.append([team, group, category, answer, dt.strftime('%Y-%m-%d %H:%M:%S')])
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    file = BufferedInputFile(buffer.read(), filename="osint_answers.xlsx")
    await bot.send_document(chat_id=ADMIN_ID, document=file, caption="📊 Все ответы команд выгружены успешно!")


# ---- Подготовить задание ----
@dp.message(F.text == "📄 Подготовить задание")
async def prepare_task(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("📤 Отправьте PDF-файл задания или выберите активное:", reply_markup=await tasks_keyboard())
    await state.set_state(TaskUpload.waiting_for_file)


@dp.message(StateFilter(TaskUpload.waiting_for_file), F.document)
async def upload_task_file(message: types.Message, state: FSMContext):
    document = message.document
    if not document.file_name.lower().endswith(".pdf"):
        return await message.answer("❗ Отправьте файл в формате PDF.")
    os.makedirs(TASKS_DIR, exist_ok=True)
    path = os.path.join(TASKS_DIR, document.file_name)
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(path):
        path = f"{base}_{i}{ext}"
        i += 1
    file = await bot.get_file(document.file_id)
    await bot.download_file(file.file_path, path)
    await message.answer(f"✅ Файл сохранён: {os.path.basename(path)}", reply_markup=await tasks_keyboard())


@dp.message(StateFilter(TaskUpload.waiting_for_file))
async def select_active_task(message: types.Message, state: FSMContext):
    if message.text == "⬅ Назад":
        await state.clear()
        return await message.answer("🔙 Возврат в меню", reply_markup=main_keyboard(ADMIN_ID))
    filename = os.path.join(TASKS_DIR, message.text)
    if not os.path.exists(filename):
        return await message.answer("❗ Файл не найден.")
    await state.update_data(filename=filename)
    await message.answer(f"Вы уверены, что хотите сделать активным файл:\n<b>{message.text}</b>?", parse_mode="HTML", reply_markup=confirm_keyboard())
    await state.set_state(TaskUpload.confirm_file)


@dp.message(StateFilter(TaskUpload.confirm_file))
async def confirm_task_selection(message: types.Message, state: FSMContext):
    data = await state.get_data()
    filename = data.get("filename")
    if message.text == "✅ Да":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('active_task', ?)", (filename,))
            await db.commit()
        await message.answer(f"📘 Активное задание установлено: {os.path.basename(filename)}", reply_markup=main_keyboard(ADMIN_ID))
        await state.clear()
    elif message.text == "❌ Нет":
        await state.clear()
        await message.answer("❌ Действие отменено.", reply_markup=main_keyboard(ADMIN_ID))
    else:
        await message.answer("Выберите: ✅ Да или ❌ Нет.")


# ---- Пользователь получает задание ----
@dp.message(F.text == "📘 Получить задание")
async def get_task(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = 'active_task'")
        row = await cursor.fetchone()
    if not row or not os.path.exists(row[0]):
        return await message.answer("❗ Активное задание не установлено.")
    file_path = row[0]
    file = BufferedInputFile(open(file_path, "rb").read(), filename="OSINT TASK.pdf")
    await message.answer_document(file, caption="📘 Ваше задание")


# ---- Все команды ----
@dp.message(F.text == "📋 Все команды")
async def all_teams(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    keyboard = await teams_keyboard()
    if not keyboard:
        return await message.answer("❗ Нет зарегистрированных команд.")
    await message.answer("📋 Выберите команду:", reply_markup=keyboard)


# ---- Просмотр команды ----
@dp.message()
async def team_info(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.text in ["📋 Все команды", "📥 Выгрузить все ответы команд", "⬅ Назад", "📄 Подготовить задание"]:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id, group_number, username, tg_link FROM users WHERE team_name = ?", (message.text,))
        members = await cursor.fetchall()

        if not members:
            return await message.answer("❗ Команда не найдена.")

        text = f"🏷️ <b>{message.text}</b>\n"
        for user_id, group_number, username, tg_link in members:
            text += f"🔢 Группа: <b>{group_number}</b>\n"
            text += f"👤 Telegram: <a href='{tg_link}'>{username or tg_link}</a>\n\n"

            cursor2 = await db.execute("SELECT category, answer, created_at FROM answers WHERE user_id = ?", (user_id,))
            answers = await cursor2.fetchall()
            if answers:
                for category, answer, created_at in answers:
                    dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
                    text += f"🏷️ <b>{category}</b>\n💬 {answer}\n⏰ {dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            else:
                text += "❗ Ответов пока нет.\n"
            text += "\n"

    await message.answer(text.strip(), parse_mode="HTML", disable_web_page_preview=False)


# ---- Запуск ----
async def main():
    await init_db()
    print("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
