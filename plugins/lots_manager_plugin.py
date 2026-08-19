from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from cardinal import Cardinal

import html
import json
import os
import re
import time
from logging import getLogger
from telebot.types import Message, CallbackQuery
from tg_bot import static_keyboards as skb

NAME = "Lots Manager Plugin"
VERSION = "0.3.0"
DESCRIPTION = ("Сортировка лотов (/sort_lots), ручной порядок (/order_lots) и "
               "список лотов (/my_lots).")
CREDITS = "FPC"
UUID = "f3a1c9e7-2b4d-4e8f-9a1c-6d5e7f8a9b0c"
SETTINGS_PAGE = False

logger = getLogger("FPC.lots_manager_plugin")

CBT_SORT_APPLY = "lm.sort.apply"
CBT_SORT_CANCEL = "lm.sort.cancel"
CBT_ORDER_APPLY = "lm.order.apply"
CBT_ORDER_CANCEL = "lm.order.cancel"
CBT_WAIT_ORDER = "lots_manager_plugin.wait_order"

ORDER_FILE = os.path.join("storage", "cache", "lot_order.json")

SORT_KEYS = {
    "id": "по ID",
    "price": "по цене",
    "amount": "по наличию",
    "title": "по названию",
    "game": "по игре",
    "subcategory": "по подкатегории",
    "auto": "по автовыдаче",
}
SORT_DIRECTIONS = ("asc", "desc")

_pending_orders: dict[int, list] = {}
_pending_lots: dict[int, list] = {}


def _esc(text) -> str:
    return html.escape(str(text)) if text else "?"


def _load_order() -> dict:
    default = {"ordered_ids": [], "sort_by": "", "sort_dir": "asc"}
    try:
        with open(ORDER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            default.update(data)
    except (OSError, json.JSONDecodeError):
        pass
    ordered = [int(i) for i in default.get("ordered_ids", []) if isinstance(i, int) or str(i).isdigit()]
    default["ordered_ids"] = ordered
    return default


def _save_order(order: dict):
    try:
        with open(ORDER_FILE, "w", encoding="utf-8") as f:
            json.dump(order, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        logger.error("[LOTS MANAGER] Failed to save lot order.")
        logger.debug("TRACEBACK", exc_info=True)
        return False


def _get_lots(cardinal) -> list | None:
    attempts = 3
    while attempts:
        try:
            profile = cardinal.account.get_user(cardinal.account.id)
            return profile.get_lots()
        except Exception:
            logger.error("[LOTS MANAGER] Failed to get account lots.")
            logger.debug("TRACEBACK", exc_info=True)
            time.sleep(1)
            attempts -= 1
    return None


def _sort_key(lot, key: str):
    if key == "price":
        return lot.price if lot.price is not None else -1
    if key == "amount":
        return lot.amount if lot.amount is not None else -1
    if key == "title":
        return (lot.title or "").lower()
    if key == "game":
        sub = lot.subcategory
        cat = sub.category if sub is not None else None
        return ((cat.name if cat is not None else "") or "").lower()
    if key == "subcategory":
        sub = lot.subcategory
        return (sub.name if sub is not None else "").lower()
    if key == "auto":
        return 0 if lot.auto else 1
    return lot.id


def _sort_lots(lots: list, key: str, direction: str) -> list:
    return sorted(lots, key=lambda lot: _sort_key(lot, key), reverse=(direction == "desc"))


def _fmt_lot_separate(lot, index: int) -> str:
    title = re.sub(r"\s+", " ", lot.title or "Без названия").strip()[:60]
    sub = lot.subcategory
    cat_name = _esc(sub.category.name if sub is not None and sub.category is not None else "?")
    sub_name = _esc(sub.name if sub is not None else "?")
    price = f"{lot.price:g}" if lot.price is not None else "—"
    amount = lot.amount if lot.amount is not None else "—"
    auto = "🤖" if lot.auto else ""
    link = lot.public_link
    return (
        f"<b>{index}.</b> <code>{lot.id}</code> {_esc(title)}\n"
        f"    💰 <b>{price}₽</b> | 📦 {amount} {auto}\n"
        f"    📂 {cat_name} / {sub_name}"
    )


def _fmt_lot_compact(lot, index: int) -> str:
    title = re.sub(r"\s+", " ", lot.title or "Без названия").strip()[:60]
    sub = lot.subcategory
    cat_name = _esc(sub.category.name if sub is not None and sub.category is not None else "?")
    sub_name = _esc(sub.name if sub is not None else "?")
    price = f"{lot.price:g}" if lot.price is not None else "—"
    amount = lot.amount if lot.amount is not None else "—"
    auto = "🤖" if lot.auto else ""
    return (
        f"<b>{index}.</b> <code>{lot.id}</code> {_esc(title)} — "
        f"{price}₽ | {amount} {auto} | {cat_name} / {sub_name}"
    )


def _ordered_lots(lots: list, order: dict) -> list:
    ordered = order.get("ordered_ids", [])
    by_id = {int(lot.id): lot for lot in lots}
    result = [by_id[i] for i in ordered if i in by_id]
    seen = {lot.id for lot in result}
    result += sorted((lot for lot in lots if lot.id not in seen), key=lambda lot: int(lot.id))
    return result


def _send_compact(bot, chat_id, lines: list[str], header: str = ""):
    parts = []
    current = []
    size = len(header) + 2
    for line in lines:
        if size + len(line) + 1 > 3900:
            parts.append(current)
            current = [line]
            size = len(header) + 2 + len(line)
        else:
            current.append(line)
            size += len(line) + 1
    if current:
        parts.append(current)
    for i, part in enumerate(parts):
        text = (header + "\n\n" if header and i == 0 else header) + "\n".join(part)
        bot.send_message(chat_id, text, parse_mode="HTML")
        time.sleep(0.3)


def init_commands(cardinal: Cardinal):
    logger.info("[LOTS MANAGER] init_commands called!")
    if not cardinal.telegram:
        logger.error("[LOTS MANAGER] cardinal.telegram = None, exiting")
        return
    tg = cardinal.telegram
    bot = cardinal.telegram.bot

    def act_my_lots(m: Message):
        lots = _get_lots(cardinal)
        if lots is None:
            bot.send_message(m.chat.id, "❌ Не удалось получить данные о лотах аккаунта.")
            return
        order = _load_order()
        lots = _ordered_lots(lots, order)
        sort_info = ""
        if order.get("sort_by"):
            sort_info = (f"\nСортировка: {SORT_KEYS.get(order['sort_by'], order['sort_by'])} "
                         f"({order['sort_dir']})")
        elif order.get("ordered_ids"):
            sort_info = "\nРучной порядок"
        if not lots:
            bot.send_message(m.chat.id, "📦 На аккаунте нет лотов.")
            return
        header = f"📦 <b>Ваши лоты: {len(lots)} шт.</b>" + sort_info
        bot.send_message(m.chat.id, header, parse_mode="HTML")
        time.sleep(0.3)
        for i, lot in enumerate(lots):
            text = _fmt_lot_separate(lot, i + 1)
            try:
                bot.send_message(m.chat.id, text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                logger.debug("TRACEBACK", exc_info=True)
                bot.send_message(m.chat.id, _fmt_lot_compact(lot, i + 1), parse_mode="HTML",
                                 disable_web_page_preview=True)
            time.sleep(0.3)

    def act_sort_lots(m: Message):
        parts = re.split(r"\s+", (m.text or "").strip())
        if len(parts) < 2:
            kb = skb.InlineKeyboardMarkup(row_width=2)
            btns = []
            for k, v in SORT_KEYS.items():
                btns.append(skb.InlineKeyboardButton(f"{v} ↑", callback_data=f"lm_sk:{k}:asc"))
                btns.append(skb.InlineKeyboardButton(f"{v} ↓", callback_data=f"lm_sk:{k}:desc"))
            kb.add(*btns)
            bot.send_message(
                m.chat.id,
                "⚙️ <b>Сортировка лотов</b>\n\n"
                "Выберите способ сортировки:",
                parse_mode="HTML", reply_markup=kb)
            return

        key = parts[1].strip().lower()
        if key not in SORT_KEYS:
            bot.send_message(m.chat.id, "❌ Неизвестный ключ. Допустимо: " +
                             ", ".join(SORT_KEYS.keys()) + ".")
            return
        direction = "asc"
        if len(parts) >= 3:
            direction = parts[2].strip().lower()
            if direction not in SORT_DIRECTIONS:
                bot.send_message(m.chat.id, "❌ Направление: asc или desc.")
                return

        _do_sort_and_preview(m.chat.id, m.from_user.id, key, direction, cardinal, bot, tg)

    def cbq_sort_key(c: CallbackQuery):
        data = c.data.split(":")
        key, direction = data[1], data[2]
        bot.answer_callback_query(c.id)
        _do_sort_and_preview(c.message.chat.id, c.from_user.id, key, direction, cardinal, bot, tg)

    def cbq_sort_apply(c: CallbackQuery):
        chat_id = c.message.chat.id
        bot.answer_callback_query(c.id, "✅ Порядок сохранён!")
        pending = _pending_orders.pop(chat_id, None)
        if pending is None:
            bot.edit_message_text("⚠️ Время действия истекло. Повторите команду.", chat_id,
                                 c.message.message_id)
            return
        order = _load_order()
        order["ordered_ids"] = [int(lot.id) for lot in pending]
        order["sort_by"] = ""
        order["sort_dir"] = ""
        _save_order(order)
        bot.edit_message_text("✅ <b>Порядок лотов сохранён!</b>\n\n"
                              "Откройте /my_lots чтобы увидеть результат.",
                              chat_id, c.message.message_id, parse_mode="HTML")

    def cbq_sort_cancel(c: CallbackQuery):
        chat_id = c.message.chat.id
        _pending_orders.pop(chat_id, None)
        _pending_lots.pop(chat_id, None)
        bot.answer_callback_query(c.id, "❌ Отменено")
        bot.edit_message_text("❌ Сортировка отменена.", chat_id, c.message.message_id)

    def act_order_lots(m: Message):
        lots = _get_lots(cardinal)
        if lots is None:
            bot.send_message(m.chat.id, "❌ Не удалось получить данные о лотах аккаунта.")
            return
        lines = [_fmt_lot_compact(lot, i + 1) for i, lot in enumerate(lots)]
        _send_compact(bot, m.chat.id, lines,
                       f"📝 <b>Текущий порядок ({len(lots)} лотов):</b>")
        time.sleep(0.3)
        bot.send_message(
            m.chat.id,
            "Отправьте ID лотов в нужном порядке через пробел или запятую.\n"
            f"Всего лотов: <b>{len(lots)}</b>.\n"
            "ID, которых нет в списке, встанут в конец.",
            parse_mode="HTML",
            reply_markup=skb.CLEAR_STATE_BTN)
        tg.set_user_state(m.chat.id, m.message_id if hasattr(m, 'message_id') else 0,
                          m.from_user.id, CBT_WAIT_ORDER)

    def save_order(m: Message):
        tg.clear_state(m.chat.id, m.from_user.id, True)
        text = re.sub(r"[^\d\s,]+", " ", m.text or "")
        ids = [int(x) for x in re.findall(r"\d+", text)]
        if not ids:
            bot.send_message(m.chat.id, "❌ Не нашёл ID лотов.")
            return

        lots = _get_lots(cardinal)
        if lots is None:
            bot.send_message(m.chat.id, "❌ Не удалось получить данные о лотах аккаунта.")
            return
        valid_ids = {int(lot.id) for lot in lots}
        unknown = [i for i in ids if i not in valid_ids]
        ids = [i for i in ids if i in valid_ids]

        ordered = [lot for lot in lots if int(lot.id) in ids]
        ordered.sort(key=lambda lot: ids.index(int(lot.id)))
        unordered = [lot for lot in lots if int(lot.id) not in valid_ids or int(lot.id) not in ids]

        preview_lines = [_fmt_lot_compact(lot, i + 1) for i, lot in enumerate(ordered + unordered)]
        _send_compact(bot, m.chat.id, preview_lines,
                       f"📝 <b>Новый порядок ({len(ordered + unordered)} лотов):</b>")

        _pending_orders[m.chat.id] = ordered + unordered
        kb = skb.InlineKeyboardMarkup()
        kb.add(skb.InlineKeyboardButton("✅ Применить", callback_data=CBT_ORDER_APPLY),
               skb.InlineKeyboardButton("❌ Отмена", callback_data=CBT_ORDER_CANCEL))
        bot.send_message(m.chat.id, "Применить этот порядок?", reply_markup=kb)

    def cbq_order_apply(c: CallbackQuery):
        chat_id = c.message.chat.id
        bot.answer_callback_query(c.id, "✅ Порядок сохранён!")
        pending = _pending_orders.pop(chat_id, None)
        if pending is None:
            bot.edit_message_text("⚠️ Время действия истекло. Повторите.", chat_id,
                                 c.message.message_id)
            return
        order = _load_order()
        order["ordered_ids"] = [int(lot.id) for lot in pending]
        order["sort_by"] = ""
        order["sort_dir"] = ""
        _save_order(order)
        bot.edit_message_text("✅ <b>Ручной порядок сохранён!</b>\n\n"
                              "Откройте /my_lots чтобы увидеть результат.",
                              chat_id, c.message.message_id, parse_mode="HTML")

    def cbq_order_cancel(c: CallbackQuery):
        chat_id = c.message.chat.id
        _pending_orders.pop(chat_id, None)
        bot.answer_callback_query(c.id, "❌ Отменено")
        bot.edit_message_text("❌ Порядок отменён.", chat_id, c.message.message_id)

    cardinal.add_telegram_commands(UUID, [
        ("my_lots", "список лотов аккаунта в сохранённом порядке", True),
        ("sort_lots", "сортировка лотов (price/amount/title/id/game/subcategory/auto)", True),
        ("order_lots", "ручной порядок лотов (какой лот после какого)", True),
    ])

    tg.msg_handler(act_my_lots, commands=["my_lots"])
    tg.msg_handler(act_sort_lots, commands=["sort_lots"])
    tg.msg_handler(act_order_lots, commands=["order_lots"])
    tg.msg_handler(save_order, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, CBT_WAIT_ORDER))

    tg.cbq_handler(cbq_sort_key, lambda c: c.data.startswith("lm_sk:"))
    tg.cbq_handler(cbq_sort_apply, lambda c: c.data == CBT_SORT_APPLY)
    tg.cbq_handler(cbq_sort_cancel, lambda c: c.data == CBT_SORT_CANCEL)
    tg.cbq_handler(cbq_order_apply, lambda c: c.data == CBT_ORDER_APPLY)
    tg.cbq_handler(cbq_order_cancel, lambda c: c.data == CBT_ORDER_CANCEL)

    logger.info("[LOTS MANAGER] All handlers registered!")


def _do_sort_and_preview(chat_id, user_id, key, direction, cardinal, bot, tg):
    lots = _get_lots(cardinal)
    if lots is None:
        bot.send_message(chat_id, "❌ Не удалось получить данные о лотах аккаунта.")
        return
    lots = _sort_lots(lots, key, direction)

    lines = [_fmt_lot_compact(lot, i + 1) for i, lot in enumerate(lots)]
    _send_compact(bot, chat_id, lines,
                   f"🔍 <b>Превью сортировки:</b> {SORT_KEYS[key]} ({direction})")

    _pending_orders[chat_id] = lots

    kb = skb.InlineKeyboardMarkup()
    kb.add(skb.InlineKeyboardButton("✅ Применить", callback_data=CBT_SORT_APPLY),
           skb.InlineKeyboardButton("❌ Отмена", callback_data=CBT_SORT_CANCEL))
    bot.send_message(chat_id, "Сохранить этот порядок?", reply_markup=kb)


BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
