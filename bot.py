"""
Агент УЦЦП — Telegram-бот для управления задачами через YouGile
@OtabekMOS_bot
"""

import os
import logging
import re
import tempfile
from datetime import datetime
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import registry

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
YOUGILE_API_KEY = os.getenv("YOUGILE_API_KEY")
YOUGILE_COMPANY_ID = os.getenv("YOUGILE_COMPANY_ID")

YOUGILE_PROJECT_ID = "fda81eb4-8535-4af7-ac4e-606666d668de"
YOUGILE_BOARD_ID = "6cd0ab65-c652-4126-be39-3f58c9536070"

YOUGILE_BASE = "https://ru.yougile.com/api-v2"
HEADERS = {
    "Authorization": f"Bearer {YOUGILE_API_KEY}",
    "Content-Type": "application/json"
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Шаблон задачи ──────────────────────────────────────────────────────────

TASK_TEMPLATE = """📋 *Новая задача:*

Скопируйте и заполните 👇

```
Задача:
Срок:
```"""

# ─── Направления и парсинг ──────────────────────────────────────────────────

DIRECTIONS = {
    "📦 Закупки": ["закуп", "поставщик", "тендер", "заказ", "контракт"],
    "🏭 Склад": ["склад", "остаток", "инвентар", "хранен", "приход", "расход", "запас"],
    "🚚 Логистика": ["логистик", "доставк", "машин", "водител", "маршрут", "перевоз"],
    "🏪 Франчайзинг": ["франч", "партнер", "франшиз", "роялти"],
    "💰 Себестоимость": ["себестоим", "стоимост", "маржа", "затрат"],
    "🏗 Открытие точек": ["открыт", "ремонт", "новая точка", "запуск", "строит", "помещен"],
}

PRIORITY_EMOJI = {
    "срочно": "🔴",
    "важно": "🟡",
    "обычно": "🟢",
    "высокий": "🔴",
    "средний": "🟡",
    "низкий": "🟢",
}

user_data_store = {}


def parse_template(text: str) -> dict:
    """Парсит заполненный шаблон задачи"""
    fields = {
        "title": "",
        "direction": "",
        "deadline": "",
        "priority": "🟢 Обычно",
        "assignee": "",
        "comment": "",
    }

    title_match = re.search(r"Задача:\s*(.+)", text, re.IGNORECASE)
    deadline_match = re.search(r"Срок:\s*(.+)", text, re.IGNORECASE)

    if title_match:
        fields["title"] = title_match.group(1).strip()
    if deadline_match:
        fields["deadline"] = deadline_match.group(1).strip()

    return fields


def deadline_to_timestamp(deadline_str: str):
    """Конвертирует строку даты в timestamp (мс)"""
    if not deadline_str:
        return None
    formats = ["%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(deadline_str.strip(), fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def create_yougile_task(fields: dict) -> dict:
    """Создаёт задачу в YouGile"""
    cols_resp = requests.get(
        f"{YOUGILE_BASE}/columns?boardId={YOUGILE_BOARD_ID}",
        headers=HEADERS
    )
    columns = cols_resp.json().get("content", [])
    if not columns:
        return {"error": "Нет колонок на доске"}

    column_id = columns[0]["id"]

    title = f"[{fields['direction']}] {fields['title']}" if fields.get("direction") else fields["title"]

    description_parts = []
    if fields.get("assignee"):
        description_parts.append(f"👤 Ответственный: {fields['assignee']}")
    if fields.get("priority"):
        description_parts.append(f"⚡ Приоритет: {fields['priority']}")
    if fields.get("comment"):
        description_parts.append(f"💬 {fields['comment']}")
    description_parts.append("📱 Создано через Агент УЦЦП")

    payload = {
        "title": title,
        "columnId": column_id,
        "description": "\n".join(description_parts),
    }

    ts = deadline_to_timestamp(fields.get("deadline", ""))
    if ts:
        payload["deadline"] = {"deadline": ts}

    resp = requests.post(f"{YOUGILE_BASE}/tasks", headers=HEADERS, json=payload)
    return resp.json()


# ─── Обработчики ────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📋 Новая задача", callback_data="new_task")],
        [InlineKeyboardButton("📊 Мои задачи", callback_data="list_tasks"),
         InlineKeyboardButton("📈 Отчёт", callback_data="report")],
        [InlineKeyboardButton("📂 Реестр документов", callback_data="docs_recent"),
         InlineKeyboardButton("📥 Выгрузить Excel", callback_data="export_docs")],
    ]
    await update.message.reply_text(
        "👋 *Агент УЦЦП* — Хрокой Душанбе\n\n"
        "Управляю задачами по направлениям:\n"
        "📦 Закупки · 🏭 Склад · 🚚 Логистика\n"
        "🏪 Франчайзинг · 💰 Себестоимость · 🏗 Открытие точек\n\n"
        "Выберите действие 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def send_template(update_or_query, context, is_callback=False):
    """Отправляет шаблон задачи"""
    if is_callback:
        await update_or_query.message.reply_text(
            TASK_TEMPLATE,
            parse_mode="Markdown"
        )
    else:
        await update_or_query.message.reply_text(
            TASK_TEMPLATE,
            parse_mode="Markdown"
        )


async def new_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TASK_TEMPLATE, parse_mode="Markdown")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "new_task":
        await query.message.reply_text(TASK_TEMPLATE, parse_mode="Markdown")

    elif query.data == "list_tasks":
        await show_tasks(query.message)

    elif query.data == "report":
        await show_report(query.message)

    elif query.data == "docs_recent":
        await docs_recent_cmd_from_callback(query.message)

    elif query.data == "export_docs":
        await export_from_callback(query.message)

    elif query.data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("📋 Новая задача", callback_data="new_task")],
            [InlineKeyboardButton("📊 Мои задачи", callback_data="list_tasks"),
             InlineKeyboardButton("📈 Отчёт", callback_data="report")],
        ]
        await query.message.reply_text(
            "Главное меню 👇",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает заполненный шаблон"""
    text = update.message.text

    # Проверяем — это заполненный шаблон?
    if "Задача:" in text:
        fields = parse_template(text)

        if not fields["title"]:
            await update.message.reply_text(
                "⚠️ Не заполнено поле *Задача*. Попробуйте снова.",
                parse_mode="Markdown"
            )
            return

        # Показываем превью перед созданием
        keyboard = [
            [
                InlineKeyboardButton("✅ Создать", callback_data=f"confirm_{update.message.message_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="main_menu"),
            ]
        ]

        user_data_store[update.effective_user.id] = fields

        preview = (
            f"📋 *Проверьте задачу:*\n\n"
            f"📌 {fields['title']}\n"
            f"📅 {fields['deadline'] or 'без срока'}\n"
        )

        await update.message.reply_text(
            preview,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    else:
        # Не шаблон — показываем подсказку
        keyboard = [[InlineKeyboardButton("📋 Открыть шаблон", callback_data="new_task")]]
        await update.message.reply_text(
            "Для создания задачи используйте шаблон 👇",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и создание задачи в YouGile"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    fields = user_data_store.get(user_id)

    if not fields:
        await query.edit_message_text("⚠️ Данные задачи не найдены. Создайте задачу заново.")
        return

    await query.edit_message_text("⏳ Создаю задачу в YouGile...")

    result = create_yougile_task(fields)

    if "id" in result:
        keyboard = [
            [InlineKeyboardButton("📋 Ещё задача", callback_data="new_task"),
             InlineKeyboardButton("📊 Все задачи", callback_data="list_tasks")]
        ]
        await query.edit_message_text(
            f"✅ *Задача создана в YouGile!*\n\n"
            f"📌 {fields['title']}\n"
            f"📅 {fields['deadline'] or 'без срока'}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        user_data_store.pop(user_id, None)
    else:
        await query.edit_message_text(f"❌ Ошибка: {result}")


async def show_tasks(message):
    resp = requests.get(
        f"{YOUGILE_BASE}/tasks?boardId={YOUGILE_BOARD_ID}",
        headers=HEADERS
    )
    tasks = [t for t in resp.json().get("content", []) if not t.get("deleted") and not t.get("completed")]

    if not tasks:
        await message.reply_text("📭 Активных задач нет.")
        return

    text = f"📊 *Активные задачи ({len(tasks)}):*\n\n"
    for t in tasks[:15]:
        title = t.get("title", "—")
        text += f"• {title}\n"

    keyboard = [[InlineKeyboardButton("📋 Новая задача", callback_data="new_task")]]
    await message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_report(message):
    resp = requests.get(f"{YOUGILE_BASE}/tasks?boardId={YOUGILE_BOARD_ID}", headers=HEADERS)
    tasks = resp.json().get("content", [])
    active = [t for t in tasks if not t.get("deleted") and not t.get("completed")]
    done = [t for t in tasks if t.get("completed")]

    by_dir = {d: 0 for d in DIRECTIONS}
    for t in active:
        title = t.get("title", "")
        for d in DIRECTIONS:
            if d in title:
                by_dir[d] += 1
                break

    text = f"📈 *Отчёт УЦЦП*\n\n✅ Выполнено: {len(done)}\n🔄 В работе: {len(active)}\n\n"
    for d, count in by_dir.items():
        if count > 0:
            text += f"{d}: {count} задач\n"

    await message.reply_text(text, parse_mode="Markdown")


async def list_tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_tasks(update.message)


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_report(update.message)


# ─── Реестр документов ───────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает фото/документ, регистрирует в реестре, хранит в Telegram"""
    user = update.effective_user
    msg = update.message

    try:
        # Определяем тип файла
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            ext = "jpg"
            file_id = msg.photo[-1].file_id
            mimetype = "image/jpeg"
        elif msg.document:
            file_obj = await msg.document.get_file()
            ext = (msg.document.file_name or "file").split(".")[-1]
            file_id = msg.document.file_id
            mimetype = msg.document.mime_type or "application/octet-stream"
        else:
            await msg.reply_text("❌ Поддерживаются только фото и документы.")
            return

        # Генерируем номер документа
        doc_number = registry.next_doc_number()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{doc_number}_{timestamp}.{ext}"

        await msg.reply_text("⏳ Загружаю в Google Drive...")

        # Скачиваем во временный файл
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp_path = tmp.name
        await file_obj.download_to_drive(tmp_path)

        # Загружаем в Google Drive
        from gdrive import upload_file
        result = upload_file(tmp_path, filename, mimetype)
        os.unlink(tmp_path)

        # Сохраняем в реестр
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        registry.save_document(
            doc_number=doc_number,
            filename=filename,
            drive_id=result["id"],
            drive_url=result["url"],
            user_id=user.id,
            username=user.username or "",
            full_name=full_name
        )

        caption = msg.caption or ""
        keyboard = [
            [InlineKeyboardButton("📂 Открыть в Drive", url=result["url"])],
            [InlineKeyboardButton("🔍 Найти", callback_data="search_docs"),
             InlineKeyboardButton("📋 Реестр", callback_data="docs_recent")]
        ]

        await msg.reply_text(
            f"✅ *Документ загружен в Google Drive*\n\n"
            f"📄 Номер: `{doc_number}`\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👤 Кто: {full_name or user.username or str(user.id)}\n"
            f"🗂 Файл: {filename}\n"
            + (f"💬 {caption}\n" if caption else ""),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Ошибка регистрации документа: {e}")
        await msg.reply_text(f"❌ Ошибка: {e}")


async def docs_search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск документа"""
    args = context.args
    if not args:
        await update.message.reply_text(
            "🔍 *Поиск документов*\n\n"
            "Используйте:\n"
            "`/find УЦЦП-2026-0001` — по номеру\n"
            "`/find 24.06.2026` — по дате\n"
            "`/find Алишер` — по пользователю",
            parse_mode="Markdown"
        )
        return

    query = " ".join(args)
    results = []

    # Определяем тип поиска
    if re.match(r"\d{1,2}\.\d{2}\.\d{4}", query):
        results = registry.search_by_date(query)
        search_type = "дате"
    elif "УЦЦП" in query.upper() or re.match(r"\d{4}", query):
        results = registry.search_by_number(query)
        search_type = "номеру"
    else:
        results = registry.search_by_user(query)
        search_type = "пользователю"

    if not results:
        await update.message.reply_text(f"📭 По запросу «{query}» ничего не найдено.")
        return

    text = f"🔍 *Результаты поиска по {search_type}:* «{query}»\n\n"
    for doc in results:
        text += (
            f"📄 `{doc['doc_number']}`\n"
            f"📅 {doc['uploaded_at']}\n"
            f"👤 {doc['full_name'] or doc['username']}\n"
            f"🔗 [Открыть]({doc['drive_url']})\n\n"
        )

    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


async def docs_recent_cmd_from_callback(message):
    await show_recent_docs(message)


async def export_from_callback(message):
    await do_export(message)


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_export(update.message)


async def do_export(message):
    """Экспорт реестра в Excel"""
    stats = registry.get_stats()
    if stats["total"] == 0:
        await update.message.reply_text("📭 Реестр пуст — нечего экспортировать.")
        return

    await update.message.reply_text("⏳ Формирую Excel-файл...")

    try:
        from export_excel import export_to_excel
        file_path = export_to_excel()

        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=os.path.basename(file_path),
                caption=(
                    f"📊 *Реестр документов УЦЦП*\n"
                    f"Всего: {stats['total']} документов\n"
                    f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                ),
                parse_mode="Markdown"
            )

        os.unlink(file_path)

    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def show_recent_docs(message):
    docs = registry.get_recent(10)
    stats = registry.get_stats()

    if not docs:
        await message.reply_text("📭 Реестр пуст. Отправьте фото для загрузки.")
        return

    text = f"📂 *Реестр документов УЦЦП*\n_Всего: {stats['total']} | Сегодня: {stats['today']}_\n\n"
    for doc in docs:
        text += (
            f"📄 `{doc['doc_number']}` — {doc['uploaded_at'][:10]}\n"
            f"👤 {doc['full_name'] or doc['username']}\n\n"
        )

    keyboard = [[InlineKeyboardButton("📥 Выгрузить Excel", callback_data="export_docs")]]
    await message.reply_text(text, parse_mode="Markdown",
                             reply_markup=InlineKeyboardMarkup(keyboard))


async def docs_recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_recent_docs(update.message)


# ─── Запуск ──────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_task_command))
    app.add_handler(CommandHandler("tasks", list_tasks_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("find", docs_search_cmd))
    app.add_handler(CommandHandler("docs", docs_recent_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CallbackQueryHandler(handle_confirm, pattern="^confirm_"))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Агент УЦЦП запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
