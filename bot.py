"""
Агент УЦЦП — Telegram-бот для управления задачами через YouGile
@OtabekMOS_bot
"""

import os
import logging
import re
import tempfile
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, time, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import registry
from iiko_client import IikoClient
import price_store

_executor = ThreadPoolExecutor(max_workers=4)

# ─── Категории документов ────────────────────────────────────────────────────
# code → (название для реестра, название папки в Google Drive)
DOC_CATEGORIES = {
    "letters":   ("📨 Служебные письма", "Служебные письма"),
    "contracts": ("📑 Договоры", "Договоры"),
    "advance":   ("💵 Авансовые отчёты", "Авансовые отчёты"),
}

# Ожидающие выбора категории фото: {user_id: {file_id, ext, mimetype, caption}}
_pending_photos = {}

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

IIKO_SERVER_URL = os.getenv("IIKO_SERVER_URL")
IIKO_LOGIN = os.getenv("IIKO_LOGIN")
IIKO_PASSWORD = os.getenv("IIKO_PASSWORD")
IIKO_CHECK_TIME = os.getenv("IIKO_CHECK_TIME", "09:00")
IIKO_CHAT_ID = os.getenv("IIKO_CHAT_ID")
# Порог значимого изменения цены (%). Меньшие колебания игнорируются как шум.
IIKO_MIN_PCT = float(os.getenv("IIKO_MIN_PCT", "1"))

# Настраиваемый отчёт «Ликвидность» (сохранён в iiko). Рассылается по расписанию.
IIKO_LIQUIDITY_REPORT = os.getenv("IIKO_LIQUIDITY_REPORT", "Отчот ликвидность Чамшед Гуляев")
IIKO_LIQUIDITY_TIME = os.getenv("IIKO_LIQUIDITY_TIME", IIKO_CHECK_TIME)  # ЧЧ:ММ, по понедельникам
IIKO_LIQUIDITY_TOP = int(os.getenv("IIKO_LIQUIDITY_TOP", "30"))         # сколько товаров в сводке

# Склады-получатели для отчёта входных (приходных) цен (GUID → название)
IIKO_INCOMING_STORES = {
    "ba144d03-0377-4c40-9bd8-2386aa2a6750": "Центральный склад",
    "a0a1832d-5214-4a64-909e-6e0505c10d10": "Цех заготовок",
    "e5733dc0-4089-4ed1-bfd8-4e972b79b60d": "Цех кондитерки",
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


# ─── Цены закупки iiko ───────────────────────────────────────────────────────

def format_price_change_message(change: dict) -> str:
    arrow = "📈" if change["delta"] > 0 else "📉"
    return (
        f"{arrow} *{change['product']}*\n"
        f"Старая цена: {change['old_price']:.2f}\n"
        f"Новая цена: {change['new_price']:.2f}\n"
        f"Изменение: {change['delta']:+.2f} ({change['pct']:+.1f}%)\n"
        f"Дата: {change['date']}"
    )


def check_iiko_price_changes() -> list[dict]:
    """Забирает свежие цены закупки из iiko, сравнивает со снимком, обновляет снимок."""
    client = IikoClient(IIKO_SERVER_URL, IIKO_LOGIN, IIKO_PASSWORD)
    try:
        client.authenticate()
        date_from = (date.today() - timedelta(days=1)).isoformat()
        date_to = date.today().isoformat()
        rows = client.get_incoming_prices(date_from, date_to)
    finally:
        client.logout()

    snapshot = price_store.load_snapshot()
    changes = []

    for row in rows:
        product = row["product"]
        new_price = row["price"]
        entry = {"price": new_price, "date": row["date"]}
        known = snapshot.get(product)

        if known is None:
            snapshot[product] = entry
            continue

        old_price = known["price"]
        delta = new_price - old_price
        pct = (delta / old_price * 100) if old_price else 0.0

        # Значимое изменение — обновляем базу и уведомляем.
        # Микроколебания ниже порога считаем шумом: базу не двигаем,
        # чтобы медленный дрейф в итоге накопился и сработал разом.
        if abs(pct) >= IIKO_MIN_PCT:
            changes.append({
                "product": product,
                "old_price": old_price,
                "new_price": new_price,
                "delta": delta,
                "pct": pct,
                "date": row["date"],
            })
            snapshot[product] = entry

    price_store.save_snapshot(snapshot)
    return changes


async def iiko_scheduled_check(context: ContextTypes.DEFAULT_TYPE):
    try:
        changes = check_iiko_price_changes()
    except Exception as e:
        logger.error(f"Ошибка проверки цен iiko: {e}")
        return

    chat_id = IIKO_CHAT_ID
    if not chat_id:
        logger.warning("IIKO_CHAT_ID не задан — уведомления о ценах некуда слать")
        return

    for change in changes:
        await context.bot.send_message(
            chat_id=chat_id,
            text=format_price_change_message(change),
            parse_mode="Markdown",
        )
    logger.info(f"Проверка цен iiko завершена: {len(changes)} изменени(й)")


async def iiko_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Проверяю закупочные цены в iiko...")
    try:
        changes = check_iiko_price_changes()
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    if not changes:
        await update.message.reply_text("✅ Изменений закупочных цен не найдено.")
        return

    for change in changes:
        await update.message.reply_text(format_price_change_message(change), parse_mode="Markdown")


# ─── Настраиваемый отчёт «Ликвидность» ───────────────────────────────────────

def _fmt_qty(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") if abs(v) >= 100 else f"{v:.1f}"


def build_liquidity_report(date_from: str, date_to: str) -> str:
    """Формирует текстовую сводку по отчёту «Ликвидность» за период."""
    client = IikoClient(IIKO_SERVER_URL, IIKO_LOGIN, IIKO_PASSWORD)
    try:
        client.authenticate()
        rows = client.run_olap_preset(IIKO_LIQUIDITY_REPORT, date_from, date_to)
    finally:
        client.logout()

    # Суммируем по товару (в отчёте строки разбиты по складам)
    agg = {}
    for r in rows:
        name = r.get("Product.Name") or "—"
        a = agg.setdefault(name, {"start": 0.0, "in": 0.0, "out": 0.0, "final": 0.0})
        a["start"] += float(r.get("StartBalance.Amount") or 0)
        a["in"] += float(r.get("Amount.In") or 0)
        a["out"] += float(r.get("Amount.Out") or 0)
        a["final"] += float(r.get("FinalBalance.Amount") or 0)

    if not agg:
        return f"📊 *Ликвидность* ({date_from}…{date_to})\n\nНет данных за период."

    # Сортируем по остатку (сколько «лежит») — самые неликвидные сверху
    items = sorted(agg.items(), key=lambda kv: kv[1]["final"], reverse=True)
    top = items[:IIKO_LIQUIDITY_TOP]

    lines = [
        f"📊 *Ликвидность* ({date_from}…{date_to})",
        f"_ТОП-{len(top)} по остатку из {len(items)} товаров · приход/расход/остаток_",
        "",
    ]
    for name, a in top:
        lines.append(
            f"• *{name}*\n"
            f"  📥 {_fmt_qty(a['in'])}  📤 {_fmt_qty(a['out'])}  📦 {_fmt_qty(a['final'])}"
        )
    return "\n".join(lines)


async def liquidity_report_scheduled(context: ContextTypes.DEFAULT_TYPE):
    # run_daily запускается ежедневно; шлём только по понедельникам
    if date.today().weekday() != 0:
        return
    if not IIKO_CHAT_ID:
        logger.warning("IIKO_CHAT_ID не задан — отчёт «Ликвидность» слать некуда")
        return
    date_from = (date.today() - timedelta(days=7)).isoformat()
    date_to = (date.today() - timedelta(days=1)).isoformat()
    try:
        text = build_liquidity_report(date_from, date_to)
    except Exception as e:
        logger.error(f"Ошибка отчёта «Ликвидность»: {e}")
        return
    await context.bot.send_message(chat_id=IIKO_CHAT_ID, text=text, parse_mode="Markdown")
    logger.info("Отчёт «Ликвидность» отправлен")


async def liquidity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Формирую отчёт «Ликвидность» за прошлую неделю...")
    date_from = (date.today() - timedelta(days=7)).isoformat()
    date_to = (date.today() - timedelta(days=1)).isoformat()
    try:
        text = build_liquidity_report(date_from, date_to)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Входные (приходные) цены по складам ─────────────────────────────────────

async def _send_chunked(message, header: str, lines: list, chunk_chars: int = 3500):
    """Шлёт header + строки, разбивая на несколько сообщений под лимит Telegram."""
    buf = header
    for ln in lines:
        if len(buf) + len(ln) + 1 > chunk_chars:
            await message.reply_text(buf, parse_mode="Markdown")
            buf = ""
        buf += ("\n" if buf else "") + ln
    if buf:
        await message.reply_text(buf, parse_mode="Markdown")


async def incoming_prices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not IIKO_SERVER_URL:
        await update.message.reply_text("iiko не настроен.")
        return
    await update.message.reply_text("⏳ Собираю входные цены по складам за текущий месяц...")

    date_from = date.today().replace(day=1).isoformat()
    date_to = date.today().isoformat()
    client = IikoClient(IIKO_SERVER_URL, IIKO_LOGIN, IIKO_PASSWORD)
    try:
        client.authenticate()
        rows = client.incoming_prices_by_store(date_from, date_to, set(IIKO_INCOMING_STORES))
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return
    finally:
        client.logout()

    by_store = {}
    for r in rows:
        by_store.setdefault(r["store_id"], []).append(r)

    for sid, label in IIKO_INCOMING_STORES.items():
        items = sorted(by_store.get(sid, []), key=lambda r: r["product"])
        header = f"🏬 *{label}* — входные цены ({date_from}…{date_to})\nТоваров: {len(items)}\n"
        if not items:
            await update.message.reply_text(header + "\nНет оприходований за период.", parse_mode="Markdown")
            continue
        lines = [f"• {r['product']}: *{r['price']:.2f}*" for r in items]
        await _send_chunked(update.message, header, lines)


# ─── Изменение входных цен по складам (было → стало) ─────────────────────────

IIKO_INCOMING_SNAPSHOT = "incoming_prices_snapshot.json"


def check_incoming_price_changes(date_from: str, date_to: str) -> dict:
    """Сверяет входные цены по 3 складам со снимком. Возвращает {store_id: [(product, old, new, pct)]}."""
    client = IikoClient(IIKO_SERVER_URL, IIKO_LOGIN, IIKO_PASSWORD)
    try:
        client.authenticate()
        rows = client.incoming_prices_by_store(date_from, date_to, set(IIKO_INCOMING_STORES))
    finally:
        client.logout()

    snap = price_store.load(IIKO_INCOMING_SNAPSHOT)
    changes = {}
    for r in rows:
        key = f"{r['store_id']}|{r['product']}"
        new_price = r["price"]
        known = snap.get(key)
        if known is None:
            snap[key] = {"price": new_price, "date": r["date"]}
            continue
        old_price = known["price"]
        if old_price == new_price:
            continue
        pct = (new_price - old_price) / old_price * 100 if old_price else 0.0
        if abs(pct) >= IIKO_MIN_PCT:
            changes.setdefault(r["store_id"], []).append((r["product"], old_price, new_price, pct))
            snap[key] = {"price": new_price, "date": r["date"]}

    price_store.save(IIKO_INCOMING_SNAPSHOT, snap)
    return changes


def format_incoming_changes(store_label: str, items: list) -> str:
    lines = [f"💱 *Изменение входных цен* — {store_label}", ""]
    for product, old, new, pct in sorted(items, key=lambda x: abs(x[3]), reverse=True):
        arrow = "🔺" if new > old else "🔻"
        lines.append(f"{arrow} {product}: было {old:.2f} → стало *{new:.2f}* ({pct:+.1f}%)")
    return "\n".join(lines)


async def incoming_price_changes_scheduled(context: ContextTypes.DEFAULT_TYPE):
    if not IIKO_CHAT_ID:
        logger.warning("IIKO_CHAT_ID не задан — изменения входных цен слать некуда")
        return
    date_from = (date.today() - timedelta(days=2)).isoformat()
    date_to = date.today().isoformat()
    try:
        changes = check_incoming_price_changes(date_from, date_to)
    except Exception as e:
        logger.error(f"Ошибка проверки входных цен: {e}")
        return
    total = 0
    for sid, label in IIKO_INCOMING_STORES.items():
        items = changes.get(sid)
        if items:
            total += len(items)
            await context.bot.send_message(
                chat_id=IIKO_CHAT_ID, text=format_incoming_changes(label, items), parse_mode="Markdown"
            )
    logger.info(f"Проверка входных цен: {total} изменени(й)")


async def incoming_changes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Проверяю изменения входных цен по складам...")
    date_from = (date.today() - timedelta(days=7)).isoformat()
    date_to = date.today().isoformat()
    try:
        changes = check_incoming_price_changes(date_from, date_to)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return
    if not any(changes.values()):
        await update.message.reply_text("✅ Изменений входных цен не найдено.")
        return
    for sid, label in IIKO_INCOMING_STORES.items():
        items = changes.get(sid)
        if items:
            await update.message.reply_text(format_incoming_changes(label, items), parse_mode="Markdown")


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
    """Принимает фото/документ и спрашивает категорию перед загрузкой."""
    user = update.effective_user
    msg = update.message

    # Определяем тип файла
    if msg.photo:
        ext = "jpg"
        file_id = msg.photo[-1].file_id
        mimetype = "image/jpeg"
    elif msg.document:
        ext = (msg.document.file_name or "file").split(".")[-1]
        file_id = msg.document.file_id
        mimetype = msg.document.mime_type or "application/octet-stream"
    else:
        await msg.reply_text("❌ Поддерживаются только фото и документы.")
        return

    # Запоминаем файл до выбора категории
    _pending_photos[user.id] = {
        "file_id": file_id,
        "ext": ext,
        "mimetype": mimetype,
        "caption": msg.caption or "",
    }

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"cat_{code}")]
        for code, (label, _) in DOC_CATEGORIES.items()
    ]

    await msg.reply_text(
        "📎 *Документ получен.*\n\nВыберите направление, к которому он относится 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """После выбора категории — загружает файл в соответствующую папку Drive."""
    query = update.callback_query
    await query.answer()
    user = query.from_user

    code = query.data.replace("cat_", "")
    if code not in DOC_CATEGORIES:
        await query.edit_message_text("❌ Неизвестная категория.")
        return

    label, folder_name = DOC_CATEGORIES[code]

    pending = _pending_photos.get(user.id)
    if not pending:
        await query.edit_message_text(
            "⚠️ Файл не найден — отправьте документ заново."
        )
        return

    ext = pending["ext"]
    mimetype = pending["mimetype"]
    caption = pending["caption"]

    try:
        await query.edit_message_text(f"⏳ Загружаю в «{label}»...")

        # Получаем файл из Telegram по file_id
        file_obj = await context.bot.get_file(pending["file_id"])

        # Генерируем номер документа
        doc_number = registry.next_doc_number()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{doc_number}_{timestamp}.{ext}"

        # Скачиваем во временный файл
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp_path = tmp.name
        await file_obj.download_to_drive(tmp_path)

        # Загружаем в папку категории (фоновый поток)
        from gdrive import upload_file
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor, upload_file, tmp_path, filename, mimetype, folder_name
        )
        os.unlink(tmp_path)

        # Сохраняем в реестр с категорией
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        registry.save_document(
            doc_number=doc_number,
            filename=filename,
            drive_id=result["id"],
            drive_url=result["url"],
            user_id=user.id,
            username=user.username or "",
            full_name=full_name,
            category=label,
        )

        _pending_photos.pop(user.id, None)

        keyboard = [
            [InlineKeyboardButton("📂 Открыть в Drive", url=result["url"])],
            [InlineKeyboardButton("📋 Реестр", callback_data="docs_recent")]
        ]

        await query.edit_message_text(
            f"✅ *Документ загружен*\n\n"
            f"🗂 Направление: {label}\n"
            f"📄 Номер: `{doc_number}`\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"👤 Кто: {full_name or user.username or str(user.id)}\n"
            f"📎 Файл: {filename}\n"
            + (f"💬 {caption}\n" if caption else ""),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Ошибка регистрации документа: {e}")
        await query.edit_message_text(f"❌ Ошибка: {e}")


async def docs_search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск документа"""
    args = context.args
    if not args:
        await update.message.reply_text(
            "🔍 *Поиск документов*\n\n"
            "Используйте:\n"
            "`/find УЦЦП-2026-0001` — по номеру\n"
            "`/find 24.06.2026` — по дате\n"
            "`/find Алишер` — по пользователю\n"
            "`/find договоры` — по направлению",
            parse_mode="Markdown"
        )
        return

    query = " ".join(args)
    results = []

    # Названия категорий для распознавания поиска по направлению
    category_names = [folder.lower() for _, folder in DOC_CATEGORIES.values()]

    # Определяем тип поиска
    if re.match(r"\d{1,2}\.\d{2}\.\d{4}", query):
        results = registry.search_by_date(query)
        search_type = "дате"
    elif "УЦЦП" in query.upper() or re.match(r"\d{4}", query):
        results = registry.search_by_number(query)
        search_type = "номеру"
    elif any(query.lower() in c or c in query.lower() for c in category_names):
        results = registry.search_by_category(query)
        search_type = "направлению"
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
        cat = doc["category"] if "category" in doc.keys() and doc["category"] else "—"
        text += (
            f"📄 `{doc['doc_number']}` — {doc['uploaded_at'][:10]}\n"
            f"🗂 {cat}\n"
            f"👤 {doc['full_name'] or doc['username']}\n\n"
        )

    keyboard = [[InlineKeyboardButton("📥 Выгрузить Excel", callback_data="export_docs")]]
    await message.reply_text(text, parse_mode="Markdown",
                             reply_markup=InlineKeyboardMarkup(keyboard))


async def docs_recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_recent_docs(update.message)


# ─── Health check сервер ─────────────────────────────────────────────────────

_bot_healthy = False


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        stats = registry.get_stats()
        if _bot_healthy:
            body = f'{{"status":"ok","docs":{stats["total"]},"today":{stats["today"]}}}'.encode()
            self.send_response(200)
        else:
            body = b'{"status":"starting"}'
            self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.getenv("HEALTH_PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Health check: http://0.0.0.0:{port}/")
    server.serve_forever()


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
    app.add_handler(CommandHandler("iiko_check", iiko_check_command))
    app.add_handler(CommandHandler("likvidnost", liquidity_command))
    app.add_handler(CommandHandler("vhodnye", incoming_prices_command))
    app.add_handler(CommandHandler("vhodnye_izm", incoming_changes_command))
    app.add_handler(CallbackQueryHandler(handle_confirm, pattern="^confirm_"))
    app.add_handler(CallbackQueryHandler(handle_category, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if IIKO_SERVER_URL:
        hour, minute = (int(p) for p in IIKO_CHECK_TIME.split(":"))
        app.job_queue.run_daily(iiko_scheduled_check, time=time(hour=hour, minute=minute))

        lh, lm = (int(p) for p in IIKO_LIQUIDITY_TIME.split(":"))
        app.job_queue.run_daily(liquidity_report_scheduled, time=time(hour=lh, minute=lm))

        app.job_queue.run_daily(incoming_price_changes_scheduled, time=time(hour=hour, minute=minute))

    # Запускаем health check в фоне
    threading.Thread(target=start_health_server, daemon=True).start()

    global _bot_healthy
    _bot_healthy = True
    logger.info("🤖 Агент УЦЦП запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
