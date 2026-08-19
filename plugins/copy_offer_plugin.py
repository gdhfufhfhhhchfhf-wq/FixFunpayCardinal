from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from cardinal import Cardinal

import html as _html
import json
import os
import random
import re
import shlex
import time
from logging import getLogger
from bs4 import BeautifulSoup
from telebot.types import Message, CallbackQuery
import requests
from tg_bot import static_keyboards as skb
import FunPayAPI.types
from FunPayAPI.common.enums import SubCategoryTypes
from FunPayAPI.common.exceptions import LotSavingError

NAME = "Copy Offer Plugin"
VERSION = "1.0.0"
DESCRIPTION = ("Интерактивное копирование лотов с редактированием перед созданием. "
               "Кастомное описание, картинки, автоответ, автовыдача, подкатегория из URL.")
CREDITS = "FPC"
UUID = "e2703592-ea86-48c8-a30c-8bb41dbfbaf1"
SETTINGS_PAGE = False

logger = getLogger("FPC.copy_offer_plugin")

CBT_WAIT_EDIT = "co_plugin.wait_edit"
CBT_WAIT_PRICE = "co_plugin.wait_price"

DRAFTS: dict[int, dict] = {}

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "copy_offer_plugin_settings.json")

DEFAULT_SETTINGS = {
    "translate": "auto",
    "desc_format": "source",
    "offer_type": "auto",
    "copy_secrets": True,
    "game": "",
    "subcategory": "",
}

TRANSLATE_VALUES = ("auto", "ru", "en", "none")
DESC_VALUES = ("source", "column", "inline")
TYPE_VALUES = ("auto", "common", "autodelivery")

FLAG_ALIASES = {
    "--game": "game",
    "--subcategory": "subcategory",
    "--type": "offer_type",
    "--desc": "desc_format",
    "--translate": "translate",
    "--secrets": "copy_secrets",
}

SUMMARY_LABELS = {"краткое описание", "короткий опис", "short description", "название"}
DESC_LABELS = {"подробное описание", "докладний опис", "detailed description", "описание"}
AMOUNT_LABELS = {"наличие", "количество", "кол-во", "amount", "available", "в наличии", "quantity", "stock"}

TITLE_STATE = "title"
DESC_STATE = "desc"
PAYMENT_STATE = "payment"
AMOUNT_STATE = "amount"
SECRETS_STATE = "secrets"
PRICE_STATE = "price"


def _esc(text) -> str:
    return _html.escape(str(text)) if text else "?"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().rstrip(":*").lower())


def _img_id(url: str) -> str | None:
    match = re.search(r"(\d+)/?$", url.rstrip("/"))
    return match.group(1) if match else None


def _extract_label(group) -> str:
    for candidate in (group.find("label"), group.find(class_="control-label"),
                      group.find(class_="form-label"), group.find("h5"), group.find("strong")):
        if candidate is None:
            continue
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    for el in group.find_all(True):
        classes = el.get("class") or []
        if isinstance(classes, str):
            classes = [classes]
        if any("label" in c.lower() for c in classes):
            text = el.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            settings.update(loaded)
    except (OSError, json.JSONDecodeError):
        pass
    if isinstance(settings.get("copy_secrets"), str):
        settings["copy_secrets"] = settings["copy_secrets"].lower() == "on"
    return settings


def _save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _validate_one(key: str, value: str):
    value = value.strip()
    if key == "translate":
        if value not in TRANSLATE_VALUES:
            raise ValueError(f"translate: допустимо {', '.join(TRANSLATE_VALUES)}")
        return value
    if key == "desc_format":
        if value not in DESC_VALUES:
            raise ValueError(f"desc: допустимо {', '.join(DESC_VALUES)}")
        return value
    if key == "offer_type":
        if value not in TYPE_VALUES:
            raise ValueError(f"type: допустимо {', '.join(TYPE_VALUES)}")
        return value
    if key == "copy_secrets":
        if value not in ("on", "off"):
            raise ValueError("secrets: допустимо on/off")
        return value == "on"
    if key in ("game", "subcategory"):
        return value
    raise ValueError(f"Неизвестный ключ «{key}»")


def _fmt_settings(s: dict) -> str:
    return (
        f"• <b>translate</b>: {_esc(s['translate'])} — перевод RU↔EN (auto/ru/en/none)\n"
        f"• <b>desc</b>: {_esc(s['desc_format'])} — формат описания (source/column/inline)\n"
        f"• <b>type</b>: {_esc(s['offer_type'])} — тип предложения (auto/common/autodelivery)\n"
        f"• <b>secrets</b>: {'on' if s.get('copy_secrets') else 'off'} — копирование автовыдачи\n"
        f"• <b>game</b>: {_esc(s['game'] or '—')} — целевая игра\n"
        f"• <b>subcategory</b>: {_esc(s['subcategory'] or '—')} — целевая подкатегория"
    )


def _parse_flags(text: str) -> tuple[dict, str]:
    flags = {}
    try:
        tokens = shlex.split(text)
    except ValueError as e:
        raise ValueError(f"Некорректные кавычки в команде: {e}")
    keep = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        key = None
        value = None
        if tok.startswith("--") and "=" in tok:
            k, _, v = tok[2:].partition("=")
            if "--" + k in FLAG_ALIASES:
                key, value = FLAG_ALIASES["--" + k], v
        elif tok in FLAG_ALIASES and i + 1 < len(tokens):
            key, value = FLAG_ALIASES[tok], tokens[i + 1]
        if key is not None:
            flags[key] = value
            i += 1 if (tok.startswith("--") and "=" in tok) else 2
            continue
        keep.append(tok)
        i += 1
    out = dict(flags)
    for key, values in (("translate", TRANSLATE_VALUES), ("desc_format", DESC_VALUES),
                        ("offer_type", TYPE_VALUES)):
        if key in out:
            v = out[key].strip().lower()
            if v not in values:
                raise ValueError(f"Некорректный {key}={out[key]}. Допустимо: {', '.join(values)}")
            out[key] = v
    if "copy_secrets" in out:
        v = out["copy_secrets"].strip().lower()
        if v not in ("on", "off"):
            raise ValueError("Некорректный secrets. Допустимо: on/off")
        out["copy_secrets"] = v == "on"
    return out, " ".join(keep)


def _detect_lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "ru"
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04ff")
    return "ru" if cyr / len(letters) > 0.35 else "en"


_TRANSLIT_TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_TRANSLIT_TABLE.update({k.upper(): v.upper() for k, v in _TRANSLIT_TABLE.items()})


def _translit_ru(text: str) -> str:
    return "".join(_TRANSLIT_TABLE.get(c, c) for c in text)


def _translate(text: str, target: str) -> str | None:
    text = text.strip()
    if not text:
        return text
    chunks = [text[i:i + 1500] for i in range(0, len(text), 1500)]
    result = []
    try:
        for chunk in chunks:
            resp = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": chunk},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            result.append("".join(seg[0] for seg in data[0] if seg and seg[0]))
    except Exception:
        logger.error("[COPY OFFER] Ошибка перевода текста.")
        logger.debug("TRACEBACK", exc_info=True)
        return None
    return "\n".join(result)


def _reformat_description(text: str, mode: str) -> str:
    if mode not in ("column", "inline") or not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    out = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if mode == "inline":
            out.append("; ".join(lines))
        else:
            out.append("\n".join(lines))
    return "\n\n".join(out)


def _extract_description(el) -> str:
    out = []
    buf = []
    br_count = 0
    for node in el.descendants:
        if isinstance(node, str):
            text = node.strip()
            if text:
                buf.append(text)
                br_count = 0
        elif node.name == "br":
            if buf:
                out.append(" ".join(buf))
                buf = []
            br_count += 1
            if br_count >= 2 and out and out[-1] != "":
                out.append("")
    if buf:
        out.append(" ".join(buf))
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def parse_buy_page(acc, lot_id: int) -> dict | None:
    response = acc.method("get", f"lots/offer?id={lot_id}", {"accept": "*/*"}, {}, raise_not_200=True)
    bs = BeautifulSoup(response.content.decode(), "lxml")

    header = bs.find("h1", class_="page-header")
    if header and _norm(header.get_text(" ", strip=True)) in (
            "предложение не найдено", "offer not found", "пропозицію не знайдено"):
        return None

    title = ""
    description = ""
    params = []
    for item in bs.find_all("div", class_="param-item"):
        h5 = item.find("h5")
        if h5 is None:
            continue
        value_el = item.find("div")
        if value_el is None:
            continue
        label = h5.get_text(" ", strip=True)
        norm = _norm(label)
        if norm in DESC_LABELS:
            value = _extract_description(value_el)
        else:
            value = value_el.get_text(" ", strip=True)
        params.append((label, value))
        if norm in SUMMARY_LABELS and not title:
            title = value
        elif norm in DESC_LABELS and not description:
            description = value

    if not title:
        h1 = bs.find("h1")
        title = h1.get_text(" ", strip=True) if h1 is not None else ""

    images = [a.get("href") for a in bs.find_all("a", class_="attachments-thumb") if a.get("href")]

    subcategory_id = None
    subcategory_name = None
    back_link = bs.find("a", class_="js-back-link")
    if back_link is not None:
        href = back_link.get("href") or ""
        parts = [p for p in href.split("/") if p]
        if parts and parts[-1].isdigit():
            subcategory_id = int(parts[-1])
        elif len(parts) >= 2 and parts[-2].isdigit():
            subcategory_id = int(parts[-2])
        subcategory_name = back_link.get_text(" ", strip=True) or None

    amount = None
    for label, value in params:
        if _norm(label) in AMOUNT_LABELS:
            digits = re.search(r"\d+", value.replace(" ", ""))
            if digits:
                amount = int(digits.group())

    return {
        "lot_id": lot_id,
        "title": title,
        "description": description,
        "params": params,
        "images": [i for i in map(_img_id, images) if i],
        "amount": amount,
        "auto_delivery": bool(bs.find("i", class_="auto-dlv-icon")),
        "subcategory_id": subcategory_id,
        "subcategory_name": subcategory_name,
    }


def _categories(acc) -> dict[int, FunPayAPI.types.Category]:
    cats = {}
    for s in acc.subcategories:
        c = s.category
        cats.setdefault(c.id, c)
    return cats


def find_category(acc, name: str) -> FunPayAPI.types.Category | None:
    norm = _norm(name)
    if not norm:
        return None
    cats = list(_categories(acc).values())
    for c in cats:
        if _norm(c.name) == norm:
            return c
    if len(norm) >= 3:
        for c in cats:
            cn = _norm(c.name)
            if norm in cn or cn in norm:
                return c
    return None


def find_subcategory(acc, name: str,
                     category: FunPayAPI.types.Category | None = None) -> FunPayAPI.types.SubCategory | None:
    norm = _norm(name)
    if not norm:
        return None
    subs = category.get_subcategories() if category is not None else acc.subcategories
    subs = [s for s in subs if s.type is SubCategoryTypes.COMMON]
    for s in subs:
        if _norm(s.name) == norm:
            return s
    if len(norm) >= 3:
        for s in subs:
            sn = _norm(s.name)
            if norm in sn or sn in norm:
                return s
    return None


def resolve_subcategory(acc, data: dict, opts: dict) -> tuple[FunPayAPI.types.SubCategory | None, str | None]:
    game_name = (opts.get("game") or "").strip()
    sub_name = (opts.get("subcategory") or "").strip()

    if game_name:
        cat = find_category(acc, game_name)
        if cat is None:
            return None, f"Игра «{game_name}» не найдена на FunPay."
        if sub_name:
            sub = find_subcategory(acc, sub_name, cat)
            if sub is None:
                return None, f"Подкатегория «{sub_name}» не найдена в игре «{cat.name}»."
            return sub, None
        if data.get("subcategory_name"):
            sub = find_subcategory(acc, data["subcategory_name"], cat)
            if sub:
                return sub, None
        subs = [s for s in cat.get_subcategories() if s.type is SubCategoryTypes.COMMON]
        if subs:
            return subs[0], None
        return None, f"В игре «{cat.name}» нет обычных подкатегорий."

    if sub_name:
        sub = find_subcategory(acc, sub_name)
        if sub is None:
            return None, f"Подкатегория «{sub_name}» не найдена на аккаунте."
        return sub, None

    if data["subcategory_id"]:
        sub = acc.get_subcategory(SubCategoryTypes.COMMON, data["subcategory_id"])
        if sub is not None:
            return sub, None
    name = data["subcategory_name"]
    if name:
        sub = find_subcategory(acc, name)
        if sub:
            return sub, None
    return None, ("Не удалось определить подкатегорию источника. "
                  "Укажите вручную: --game \"Игра\" --subcategory \"Подкатегория\".")


def _parse_offer_form_html(html_content: str) -> tuple[dict | None, dict]:
    bs = BeautifulSoup(html_content, "lxml")
    form = bs.find("form", class_="form-offer-editor")
    if form is None:
        return None, {}

    result = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if not name or name == "query":
            continue
        result[name] = field.get("value") or ""
    for field in form.find_all("textarea"):
        name = field.get("name")
        if name:
            result[name] = field.text or ""
    for field in form.find_all("select"):
        name = field.get("name")
        if not name:
            continue
        option = field.find("option", selected=True)
        if not option:
            for opt in field.find_all("option"):
                if opt.get("value", ""):
                    option = opt
                    break
            if not option:
                option = field.find("option")
        result[name] = option.get("value") if option is not None else ""
    for field in form.find_all("input", {"type": "checkbox"}, checked=True):
        name = field.get("name")
        if name:
            result[name] = "on"

    label_map = {}
    for group in form.find_all("div", class_="form-group"):
        if "hidden" in group.get("class", []):
            continue
        control = group.find("input") or group.find("select") or group.find("textarea")
        name = control.get("name") if control is not None else None
        if not name or name == "query":
            continue
        label = _extract_label(group)
        if label:
            label_map.setdefault(_norm(label), name)

    return result, label_map


def parse_edit_form(acc, lot_id: int) -> tuple[dict | None, dict]:
    response = acc.method("get", f"lots/offerEdit?offer={lot_id}", {"accept": "*/*"}, {}, raise_not_200=True)
    return _parse_offer_form_html(response.content.decode())


def parse_create_form(acc, node_id: int) -> tuple[dict | None, dict]:
    response = acc.method("get", f"lots/offerEdit?node={node_id}", {"accept": "*/*"}, {}, raise_not_200=True)
    return _parse_offer_form_html(response.content.decode())


def build_fields(acc, own_lot, data: dict, subcategory: FunPayAPI.types.SubCategory, opts: dict):
    if own_lot is not None:
        skeleton, label_map = parse_edit_form(acc, own_lot.id)
    else:
        skeleton, label_map = parse_create_form(acc, subcategory.id)
    status = "ok" if label_map else "fallback"
    if not skeleton:
        if own_lot is not None:
            try:
                skeleton = acc.get_lot_fields(own_lot.id).fields
            except Exception:
                logger.error("[COPY OFFER] Не удалось получить поля собственного лота.")
                logger.debug("TRACEBACK", exc_info=True)
                skeleton = None
        if not skeleton:
            skeleton = {
                "offer_id": "0",
                "node_id": str(subcategory.id),
                "fields[summary][ru]": "",
                "fields[summary][en]": "",
                "fields[desc][ru]": "",
                "fields[desc][en]": "",
                "fields[payment_msg][ru]": "",
                "fields[payment_msg][en]": "",
                "fields[images]": "",
                "active": "on",
            }
        status = "fallback"

    fields = dict(skeleton)
    warnings = []

    translate_mode = opts.get("translate", "auto")
    desc_mode = opts.get("desc_format", "source")

    def apply_desc(text: str) -> str:
        if not text:
            return text
        return _reformat_description(text, desc_mode)

    src_summary = data["title"] or ""
    src_desc = data["description"] or ""

    summary_ru = summary_en = desc_ru = desc_en = ""
    if translate_mode == "none":
        summary_ru, desc_ru = src_summary, src_desc
        summary_en = fields.get("fields[summary][en]", "")
        desc_en = fields.get("fields[desc][en]", "")
    else:
        source_lang = _detect_lang(src_summary + "\n" + src_desc)
        is_ru = (translate_mode == "ru") or (translate_mode == "auto" and source_lang == "ru")
        if is_ru:
            summary_ru, desc_ru = src_summary, src_desc
            summary_en = _translate(src_summary, "en") or fields.get("fields[summary][en]", "")
            desc_en = _translate(src_desc, "en") or fields.get("fields[desc][en]", "")
            if not summary_en or not desc_en:
                warnings.append("Перевод RU->EN не удался — английские поля продублируются русским текстом.")
        else:
            summary_en, desc_en = src_summary, src_desc
            summary_ru = _translate(src_summary, "ru") or fields.get("fields[summary][ru]", "")
            desc_ru = _translate(src_desc, "ru") or fields.get("fields[desc][ru]", "")
            if not summary_ru or not desc_ru:
                warnings.append("Перевод EN->RU не удался — русские поля продублируются английским текстом.")

    if not summary_en and summary_ru:
        if _detect_lang(summary_ru) == "ru":
            summary_en = _translit_ru(summary_ru)
            warnings.append("Английское название заполнено транслитерацией русского текста.")
        else:
            summary_en = summary_ru
    if not desc_en and desc_ru:
        desc_en = _translit_ru(desc_ru) if _detect_lang(desc_ru) == "ru" else desc_ru
    if not summary_ru and summary_en:
        summary_ru = summary_en
    if not desc_ru and desc_en:
        desc_ru = desc_en

    fields["fields[summary][ru]"] = summary_ru
    fields["fields[summary][en]"] = summary_en
    fields["fields[desc][ru]"] = apply_desc(desc_ru)
    fields["fields[desc][en]"] = apply_desc(desc_en)

    if data["images"]:
        fields["fields[images]"] = ",".join(data["images"])
    fields["offer_id"] = "0"
    fields["node_id"] = str(subcategory.id)
    fields["active"] = "on"
    fields["secrets"] = ""
    fields.pop("auto_delivery", None)
    fields.pop("deactivate_after_sale", None)
    fields.pop("csrf_token", None)

    for label, value in data["params"]:
        norm = _norm(label)
        if norm in SUMMARY_LABELS or norm in DESC_LABELS or norm in AMOUNT_LABELS:
            continue
        name = label_map.get(norm)
        if name is None:
            for key, mapped in label_map.items():
                if len(norm) >= 3 and (norm in key or key in norm):
                    name = mapped
                    break
        if name and name not in ("fields[summary][ru]", "fields[summary][en]",
                                 "fields[desc][ru]", "fields[desc][en]", "fields[images]"):
            fields[name] = value

    offer_type = opts.get("offer_type", "auto")
    copy_secrets = bool(opts.get("copy_secrets", True))
    source_ad = bool(data.get("auto_delivery"))
    want_ad = (offer_type == "autodelivery") or (offer_type == "auto" and source_ad and copy_secrets)
    if want_ad:
        secrets = []
        try:
            src_lf = acc.get_lot_fields(data["lot_id"])
            secrets = [s for s in src_lf.secrets if s.strip()]
        except Exception:
            logger.info("[COPY OFFER] Автовыдача: нет доступа к товарам исходного лота "
                        f"({data['lot_id']}), создаю обычный лот.")
        if secrets:
            fields["secrets"] = "\n".join(secrets)
            fields["auto_delivery"] = "on"
            fields["amount"] = str(len(secrets))
            warnings.append(f"Автовыдача скопирована: {len(secrets)} шт.")
        else:
            fields["secrets"] = ""
            warnings.append("Автовыдачу скопировать нельзя (нет доступа к товарам исходного лота).")
    elif offer_type == "common" and source_ad:
        warnings.append("Исходный лот с автовыдачей — создаётся обычный лот.")

    if data["amount"] is not None and "auto_delivery" not in fields:
        fields["amount"] = str(data["amount"])

    return fields, status, warnings


def _format_save_error(e: Exception) -> str:
    if isinstance(e, LotSavingError):
        parts = []
        if e.error_message:
            parts.append(e.error_message)
        if e.errors:
            parts.append("; ".join(f"{key}: {value}" for key, value in e.errors.items()))
        return " | ".join(parts) if parts else "Неизвестная ошибка сохранения."
    return f"{type(e).__name__}: {e}"


def save_with_fallback(acc, lot) -> tuple[bool, str | None]:
    try:
        acc.save_lot(lot)
        return True, None
    except Exception as e:
        if lot.images:
            lot.images = []
            time.sleep(1)
            try:
                acc.save_lot(lot)
                return True, "Картинки не скопировались (создано без них)."
            except Exception as e2:
                return False, _format_save_error(e2)
        return False, _format_save_error(e)


def _build_preview(draft: dict) -> str:
    f = draft["fields"]
    sub = draft.get("subcategory")
    title_ru = f.get("fields[summary][ru]", "") or "—"
    title_en = f.get("fields[summary][en]", "") or "—"
    desc_ru = f.get("fields[desc][ru]", "") or ""
    desc_en = f.get("fields[desc][en]", "") or ""
    images_raw = f.get("fields[images]", "")
    image_ids = [i for i in images_raw.split(",") if i] if images_raw else []
    auto = f.get("auto_delivery") == "on"
    secrets_raw = f.get("secrets", "")
    secrets = [s for s in secrets_raw.strip().split("\n") if s] if secrets_raw else []
    amount = f.get("amount", "—")
    price = draft.get("price", "—")
    pay_ru = f.get("fields[payment_msg][ru]", "")
    pay_en = f.get("fields[payment_msg][en]", "")

    sub_name = sub.ui_name if sub else "—"

    lines = [
        f"📋 <b>Лот для копирования</b>",
        f"",
        f"🏷 <b>Название RU:</b> {_esc(title_ru[:60])}",
        f"🏷 <b>Название EN:</b> {_esc(title_en[:60])}",
        f"📂 <b>Подкатегория:</b> {_esc(sub_name)}",
        f"🖼 <b>Картинок:</b> {len(image_ids)}",
        f"🤖 <b>Автовыдача:</b> {'✅ (' + str(len(secrets)) + ' ключей)' if auto else '❌'}",
        f"📦 <b>Наличие:</b> {amount}",
        f"💰 <b>Цена:</b> {price}₽",
    ]

    if pay_ru or pay_en:
        lines.append(f"💬 <b>Автоответ RU:</b> {_esc((pay_ru or '—')[:60])}")
        lines.append(f"💬 <b>Автоответ EN:</b> {_esc((pay_en or '—')[:60])}")

    if desc_ru:
        preview = desc_ru[:150].replace("\n", " ")
        if len(desc_ru) > 150:
            preview += "…"
        lines.append(f"")
        lines.append(f"📝 <b>Описание (RU):</b>")
        lines.append(f"<code>{_esc(preview)}</code>")

    return "\n".join(lines)


def _build_edit_kb(draft: dict) -> skb.K:
    f = draft["fields"]
    auto = f.get("auto_delivery") == "on"
    img_mode = draft.get("images_mode", "keep")
    img_label = {"keep": "есть", "shuffle": "🔀", "remove": "нет"}.get(img_mode, "?")

    kb = skb.K(row_width=2)

    kb.row(
        skb.B("🏷 Название", callback_data="co.title"),
        skb.B("📝 Описание", callback_data="co.desc"),
    )
    kb.row(
        skb.B(f"🖼 Картинки: {img_label}", callback_data="co.img"),
        skb.B(
            f"🤖 Автовыдача: {'✅' if auto else '❌'}",
            callback_data="co.ad"),
    )
    kb.row(
        skb.B("💬 Автоответ", callback_data="co.pay"),
        skb.B("📦 Количество", callback_data="co.amt"),
    )
    kb.row(
        skb.B("🔑 Ключи", callback_data="co.secr"),
        skb.B("📂 Подкатегория", callback_data="co.sub"),
    )
    kb.row(
        skb.B("✅ Создать лот", callback_data="co.create"),
        skb.B("❌ Отмена", callback_data="co.cancel"),
    )
    return kb


def _sync_fields_images(draft: dict):
    mode = draft.get("images_mode", "keep")
    source_data = draft.get("source_data", {})
    source_images = list(source_data.get("images", []))
    if mode == "remove":
        draft["fields"]["fields[images]"] = ""
    elif mode == "shuffle":
        shuffled = list(source_images)
        random.shuffle(shuffled)
        draft["fields"]["fields[images]"] = ",".join(shuffled)
    else:
        draft["fields"]["fields[images]"] = ",".join(source_images)


def init_commands(cardinal: Cardinal):
    if not cardinal.telegram:
        return
    tg = cardinal.telegram
    bot = cardinal.telegram.bot

    def act_copy_lot(m: Message):
        text = (m.text or "").strip()
        try:
            flags, clean = _parse_flags(text)
        except ValueError as e:
            bot.send_message(m.chat.id, f"❌ {e}")
            return

        settings = _load_settings()
        opts = {**settings, **flags}

        match = re.search(r"id=(\d+)", clean)
        if match:
            lot_id = int(match.group(1))
        else:
            digits = re.findall(r"\d{4,}", clean)
            if not digits:
                bot.send_message(m.chat.id,
                                 "❌ Не удалось найти ID лота. Пример: /copy_lot https://funpay.com/lots/offer?id=12345678")
                return
            lot_id = int(digits[-1])

        bot.send_message(m.chat.id, f"⏳ Получаю данные лота {lot_id}...")
        try:
            data = parse_buy_page(cardinal.account, lot_id)
        except Exception:
            logger.error(f"[COPY OFFER] Не удалось получить страницу лота {lot_id}.")
            logger.debug("TRACEBACK", exc_info=True)
            bot.send_message(m.chat.id, f"❌ Не удалось получить страницу лота {lot_id}.")
            return

        if data is None:
            bot.send_message(m.chat.id,
                             f"❌ Лот <a href='https://funpay.com/lots/offer?id={lot_id}'>{lot_id}</a> не найден.",
                             parse_mode="HTML")
            return

        subcategory, error = resolve_subcategory(cardinal.account, data, opts)
        if subcategory is None:
            bot.send_message(m.chat.id, "❌ " + (error or "Не удалось определить подкатегорию."))
            return
        if subcategory.type is SubCategoryTypes.CURRENCY:
            bot.send_message(m.chat.id, "❌ Копирование лотов-валюты не поддерживается.")
            return

        bot.send_message(m.chat.id, "🔍 Ищу собственный лот для каркаса полей...")
        try:
            profile = cardinal.account.get_user(cardinal.account.id)
        except Exception:
            logger.error("[COPY OFFER] Не удалось получить профиль аккаунта.")
            logger.debug("TRACEBACK", exc_info=True)
            bot.send_message(m.chat.id, "❌ Не удалось получить данные аккаунта.")
            return

        own_lot = None
        for lot in profile.get_lots():
            if lot.subcategory is not None and lot.subcategory.id == subcategory.id:
                own_lot = lot
                break
        if own_lot is None:
            bot.send_message(m.chat.id, "ℹ️ В этой подкатегории нет ваших лотов — "
                                        "возьму каркас из формы создания.")

        try:
            fields, status, warnings = build_fields(cardinal.account, own_lot, data, subcategory, opts)
        except Exception:
            logger.error(f"[COPY OFFER] Не удалось собрать поля для копирования лота {lot_id}.")
            logger.debug("TRACEBACK", exc_info=True)
            bot.send_message(m.chat.id, "❌ Не удалось получить форму подкатегории (лимит запросов FunPay). "
                                        "Попробуйте позже.")
            return

        draft = {
            "fields": fields,
            "subcategory": subcategory,
            "source_id": lot_id,
            "source_data": data,
            "images_mode": "keep",
            "editing": None,
        }
        DRAFTS[m.from_user.id] = draft

        preview = _build_preview(draft)
        kb = _build_edit_kb(draft)

        if warnings:
            preview += "\n\n" + "\n".join(f"⚠️ {_esc(w)}" for w in warnings)

        preview += "\n\n<b>Редактируйте перед созданием или сразу создайте:</b>"

        bot.send_message(m.chat.id, preview, parse_mode="HTML", reply_markup=kb)

    def _show_menu(chat_id: int, user_id: int, extra: str = ""):
        draft = DRAFTS.get(user_id)
        if not draft:
            return
        preview = _build_preview(draft)
        if extra:
            preview += f"\n\n{extra}"
        kb = _build_edit_kb(draft)
        try:
            bot.send_message(chat_id, preview, parse_mode="HTML", reply_markup=kb)
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)

    def cbq_edit_title(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк. Начните заново.")
            return
        draft["editing"] = TITLE_STATE
        bot.answer_callback_query(c.id)
        bot.edit_message_text(
            "🏷 Отправьте новое <b>название</b> для лота (на русском).\n\n"
            "Или отправьте <b>скопировать</b> чтобы взять из источника.",
            chat_id, c.message.message_id, parse_mode="HTML")
        tg.set_user_state(chat_id, c.message.message_id, user_id, CBT_WAIT_EDIT)

    def cbq_edit_desc(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        draft["editing"] = DESC_STATE
        bot.answer_callback_query(c.id)
        bot.edit_message_text(
            "📝 Отправьте новое <b>описание</b> для лота.\n\n"
            "Или отправьте:\n"
            "• <b>скопировать</b> — взять из источника\n"
            "• <b>транслит</b> — скопировать и транслитерировать (чтобы не выглядело как копия)",
            chat_id, c.message.message_id, parse_mode="HTML")
        tg.set_user_state(chat_id, c.message.message_id, user_id, CBT_WAIT_EDIT)

    def cbq_edit_images(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        mode = draft.get("images_mode", "keep")
        if mode == "keep":
            draft["images_mode"] = "shuffle"
            _sync_fields_images(draft)
            bot.answer_callback_query(c.id, "🖼 Картинки перемешаны")
        elif mode == "shuffle":
            draft["images_mode"] = "remove"
            _sync_fields_images(draft)
            bot.answer_callback_query(c.id, "🖼 Картинки убраны")
        else:
            draft["images_mode"] = "keep"
            _sync_fields_images(draft)
            bot.answer_callback_query(c.id, "🖼 Картинки как у источника")
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=_build_edit_kb(draft))

    def cbq_edit_payment(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        draft["editing"] = PAYMENT_STATE
        bot.answer_callback_query(c.id)
        bot.edit_message_text(
            "💬 Отправьте <b>автоответ</b> (сообщение покупателю после оплаты).\n\n"
            "Или отправьте <b>убрать</b> чтобы очистить.",
            chat_id, c.message.message_id, parse_mode="HTML")
        tg.set_user_state(chat_id, c.message.message_id, user_id, CBT_WAIT_EDIT)

    def cbq_edit_amount(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        draft["editing"] = AMOUNT_STATE
        bot.answer_callback_query(c.id)
        bot.edit_message_text(
            "📦 Отправьте <b>количество</b> товара (число).\n\n"
            "Или отправьте <b>скопировать</b> чтобы взять из источника.",
            chat_id, c.message.message_id, parse_mode="HTML")
        tg.set_user_state(chat_id, c.message.message_id, user_id, CBT_WAIT_EDIT)

    def cbq_edit_secrets(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        draft["editing"] = SECRETS_STATE
        bot.answer_callback_query(c.id)
        kb = skb.K(row_width=1)
        kb.add(
            skb.B("🔑 Забрать ключи с источника", callback_data="co.get_keys"),
            skb.B("❌ Очистить ключи", callback_data="co.clear_keys"),
            skb.B("◀️ Назад", callback_data="co.back"),
        )
        src = draft.get("source_data", {})
        has_ad = src.get("auto_delivery", False)
        secrets_raw = draft["fields"].get("secrets", "")
        current = len([s for s in secrets_raw.strip().split("\n") if s]) if secrets_raw else 0
        text = (f"🔑 <b>Автовыдача</b>\n\n"
                f"Источник: {'✅ есть автовыдача' if has_ad else '❌ нет автовыдачи'}\n"
                f"Текущее кол-во ключей: {current}\n\n"
                f"Отправьте ключи (по одному на строку) или используйте кнопки:")
        bot.edit_message_text(text, chat_id, c.message.message_id, parse_mode="HTML", reply_markup=kb)

    def cbq_get_keys(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        source_id = draft.get("source_id", 0)
        bot.answer_callback_query(c.id, "⏳ Получаю ключи...")
        try:
            src_lf = cardinal.account.get_lot_fields(source_id)
            secrets = [s for s in src_lf.secrets if s.strip()]
        except Exception:
            logger.debug("TRACEBACK", exc_info=True)
            bot.edit_message_text(
                "❌ Не удалось получить ключи. Возможно, это не ваш лот или нет доступа.\n\n"
                "Отправьте ключи вручную (по одному на строку):",
                chat_id, c.message.message_id, parse_mode="HTML")
            return

        if not secrets:
            bot.edit_message_text(
                "ℹ️ У исходного лота нет ключей для автовыдачи.\n\n"
                "Отправьте ключи вручную (по одному на строку):",
                chat_id, c.message.message_id, parse_mode="HTML")
            return

        draft["fields"]["secrets"] = "\n".join(secrets)
        draft["fields"]["auto_delivery"] = "on"
        draft["fields"]["amount"] = str(len(secrets))
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=_build_edit_kb(draft))
        bot.send_message(chat_id, f"✅ Получено {len(secrets)} ключей с источника!",
                         parse_mode="HTML")
        tg.clear_state(chat_id, user_id, True)

    def cbq_clear_keys(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        draft["fields"]["secrets"] = ""
        draft["fields"]["auto_delivery"] = ""
        if "amount" in draft["fields"]:
            src_amount = draft.get("source_data", {}).get("amount")
            if src_amount is not None:
                draft["fields"]["amount"] = str(src_amount)
            else:
                draft["fields"].pop("amount", None)
        bot.answer_callback_query(c.id, "🗑 Ключи очищены")
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=_build_edit_kb(draft))

    def cbq_toggle_auto(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        current = draft["fields"].get("auto_delivery") == "on"
        if current:
            draft["fields"]["auto_delivery"] = ""
            bot.answer_callback_query(c.id, "🤖 Автовыдача выключена")
        else:
            draft["fields"]["auto_delivery"] = "on"
            secrets_raw = draft["fields"].get("secrets", "")
            secrets = [s for s in secrets_raw.strip().split("\n") if s] if secrets_raw else []
            if secrets:
                draft["fields"]["amount"] = str(len(secrets))
            bot.answer_callback_query(c.id, "🤖 Автовыдача включена")
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=_build_edit_kb(draft))

    def cbq_edit_price(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        draft["editing"] = PRICE_STATE
        bot.answer_callback_query(c.id)
        bot.edit_message_text(
            "💰 Отправьте <b>цену</b> за 1 шт. в рублях (например: 100):",
            chat_id, c.message.message_id, parse_mode="HTML")
        tg.set_user_state(chat_id, c.message.message_id, user_id, CBT_WAIT_EDIT)

    def cbq_edit_subcategory(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        bot.answer_callback_query(c.id)
        current_sub = draft.get("subcategory")
        current_name = current_sub.ui_name if current_sub else "—"

        cats = _categories(cardinal.account)
        kb = skb.K(row_width=1)
        for cat in sorted(cats.values(), key=lambda c: c.name):
            common_subs = [s for s in cat.get_subcategories() if s.type is SubCategoryTypes.COMMON]
            if not common_subs:
                continue
            kb.row(skb.B(f"🎮 {cat.name}", callback_data=f"co.cat:{cat.id}"))

        kb.row(skb.B("◀️ Назад", callback_data="co.back"))

        bot.edit_message_text(
            f"📂 <b>Текущая подкатегория:</b> {_esc(current_name)}\n\n"
            f"Выберите категорию (игру), затем подкатегорию:",
            chat_id, c.message.message_id, parse_mode="HTML", reply_markup=kb)

    def cbq_select_category(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        cat_id = int(c.data.split(":")[1])
        cats = _categories(cardinal.account)
        cat = cats.get(cat_id)
        if not cat:
            bot.answer_callback_query(c.id, "❌ Категория не найдена.")
            return
        bot.answer_callback_query(c.id)

        kb = skb.K(row_width=1)
        common_subs = [s for s in cat.get_subcategories() if s.type is SubCategoryTypes.COMMON]
        for sub in common_subs:
            kb.row(skb.B(
                sub.name,
                callback_data=f"co.sub:{sub.id}"))
        kb.row(skb.B("◀️ Назад", callback_data="co.back"))

        bot.edit_message_text(
            f"📂 <b>{_esc(cat.name)}</b> — выберите подкатегорию:",
            chat_id, c.message.message_id, parse_mode="HTML", reply_markup=kb)

    def cbq_select_subcategory(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        sub_id = int(c.data.split(":")[1])

        all_subs = cardinal.account.subcategories
        target_sub = None
        for s in all_subs:
            if s.id == sub_id:
                target_sub = s
                break

        if not target_sub:
            bot.answer_callback_query(c.id, "❌ Подкатегория не найдена.")
            return

        draft["subcategory"] = target_sub
        draft["fields"]["node_id"] = str(sub_id)

        bot.answer_callback_query(c.id, f"✅ {target_sub.ui_name}")
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=_build_edit_kb(draft))

    def cbq_create(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк. Начните заново.")
            return
        draft["editing"] = PRICE_STATE
        bot.answer_callback_query(c.id)
        preview = _build_preview(draft)
        bot.edit_message_text(
            preview + "\n\n💰 Отправьте <b>цену</b> за 1 шт. в рублях:",
            chat_id, c.message.message_id, parse_mode="HTML")
        tg.set_user_state(chat_id, c.message.message_id, user_id, CBT_WAIT_PRICE)

    def cbq_cancel(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        DRAFTS.pop(user_id, None)
        tg.clear_state(chat_id, user_id, True)
        bot.answer_callback_query(c.id, "❌ Отменено")
        bot.edit_message_text("❌ Копирование отменено.", chat_id, c.message.message_id)

    def cbq_back(c: CallbackQuery):
        user_id = c.from_user.id
        chat_id = c.message.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            bot.answer_callback_query(c.id, "⚠️ Черновик истёк.")
            return
        bot.answer_callback_query(c.id)
        tg.clear_state(chat_id, user_id, True)
        preview = _build_preview(draft)
        kb = _build_edit_kb(draft)
        bot.edit_message_text(
            preview + "\n\n<b>Редактируйте перед созданием или сразу создайте:</b>",
            chat_id, c.message.message_id, parse_mode="HTML", reply_markup=kb)

    def wait_edit_text(m: Message):
        user_id = m.from_user.id
        chat_id = m.chat.id
        draft = DRAFTS.get(user_id)
        if not draft:
            tg.clear_state(chat_id, user_id, True)
            return

        editing = draft.get("editing")
        text = (m.text or "").strip()
        f = draft["fields"]

        if editing == TITLE_STATE:
            if text.lower() == "скопировать":
                bot.send_message(chat_id, "📋 Название взято из источника.", parse_mode="HTML")
            else:
                if _detect_lang(text) == "ru":
                    f["fields[summary][ru]"] = text
                    translated = _translate(text, "en")
                    f["fields[summary][en]"] = translated if translated else _translit_ru(text)
                else:
                    f["fields[summary][en]"] = text
                    translated = _translate(text, "ru")
                    f["fields[summary][ru]"] = translated if translated else _translit_ru(text)
                bot.send_message(chat_id, "✅ Название обновлено.", parse_mode="HTML")

        elif editing == DESC_STATE:
            if text.lower() == "скопировать":
                bot.send_message(chat_id, "📋 Описание взято из источника.", parse_mode="HTML")
            elif text.lower() == "транслит":
                src = draft.get("source_data", {})
                src_desc = src.get("description", "")
                f["fields[desc][ru]"] = src_desc
                translit = _translit_ru(src_desc)
                f["fields[desc][en]"] = translit
                bot.send_message(chat_id, "✅ Описание скопировано и транслитерировано.", parse_mode="HTML")
            else:
                if _detect_lang(text) == "ru":
                    f["fields[desc][ru]"] = text
                    translated = _translate(text, "en")
                    f["fields[desc][en]"] = translated if translated else _translit_ru(text)
                else:
                    f["fields[desc][en]"] = text
                    translated = _translate(text, "ru")
                    f["fields[desc][ru]"] = translated if translated else text
                bot.send_message(chat_id, "✅ Описание обновлено.", parse_mode="HTML")

        elif editing == PAYMENT_STATE:
            if text.lower() == "убрать":
                f["fields[payment_msg][ru]"] = ""
                f["fields[payment_msg][en]"] = ""
                bot.send_message(chat_id, "🗑 Автоответ очищен.", parse_mode="HTML")
            else:
                if _detect_lang(text) == "ru":
                    f["fields[payment_msg][ru]"] = text
                    translated = _translate(text, "en")
                    f["fields[payment_msg][en]"] = translated if translated else _translit_ru(text)
                else:
                    f["fields[payment_msg][en]"] = text
                    translated = _translate(text, "ru")
                    f["fields[payment_msg][ru]"] = translated if translated else text
                bot.send_message(chat_id, "✅ Автоответ обновлён.", parse_mode="HTML")

        elif editing == AMOUNT_STATE:
            if text.lower() == "скопировать":
                src = draft.get("source_data", {})
                src_amount = src.get("amount")
                if src_amount is not None:
                    f["amount"] = str(src_amount)
                    bot.send_message(chat_id, f"📦 Количество: {src_amount}", parse_mode="HTML")
                else:
                    bot.send_message(chat_id, "ℹ️ У источника нет информации о количестве.", parse_mode="HTML")
            else:
                cleaned = text.replace(" ", "").replace(",", ".")
                try:
                    amount = int(cleaned)
                    if amount < 0:
                        raise ValueError
                    f["amount"] = str(amount)
                    bot.send_message(chat_id, f"✅ Количество: {amount}", parse_mode="HTML")
                except ValueError:
                    bot.send_message(chat_id, "❌ Отправьте целое число (например: 10) или «скопировать».")
                    return

        elif editing == SECRETS_STATE:
            if text.lower() == "убрать":
                f["secrets"] = ""
                f["auto_delivery"] = ""
                if "amount" in f:
                    src_amount = draft.get("source_data", {}).get("amount")
                    if src_amount is not None:
                        f["amount"] = str(src_amount)
                    else:
                        f.pop("amount", None)
                bot.send_message(chat_id, "🗑 Ключи очищены.", parse_mode="HTML")
            else:
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                if lines:
                    f["secrets"] = "\n".join(lines)
                    f["auto_delivery"] = "on"
                    f["amount"] = str(len(lines))
                    bot.send_message(chat_id, f"✅ Добавлено {len(lines)} ключей. Автовыдача включена.",
                                     parse_mode="HTML")
                else:
                    bot.send_message(chat_id, "❌ Не нашёл ключей. Отправьте по одному на строку.")
                    return

        elif editing == PRICE_STATE:
            cleaned = text.replace(" ", "").replace(",", ".")
            try:
                price = float(cleaned)
                if price <= 0 or price > 1_000_000:
                    raise ValueError
            except ValueError:
                bot.send_message(chat_id, "❌ Некорректная цена. Отправьте число больше 0.")
                return

            draft["price"] = price
            tg.clear_state(chat_id, user_id, True)

            fields = dict(f)
            fields["price"] = str(price)
            lot = FunPayAPI.types.LotFields(0, fields, draft["subcategory"])
            lot.price = price
            lot.active = True

            bot.send_message(chat_id, "⏳ Создаю лот на FunPay...")
            ok, note = save_with_fallback(cardinal.account, lot)
            if not ok:
                bot.send_message(chat_id, "❌ Не удалось создать лот. " + (note or "Неизвестная ошибка."))
                return

            msg = "✅ Лот создан!"
            if note:
                msg += f"\n⚠️ {note}"
            sub_name = draft["subcategory"].ui_name if draft["subcategory"] else "—"
            msg += f"\n📂 {sub_name}"
            msg += f"\n💰 {price}₽"
            bot.send_message(chat_id, msg, parse_mode="HTML")
            DRAFTS.pop(user_id, None)
            return

        draft["editing"] = None
        tg.clear_state(chat_id, user_id, True)
        _show_menu(chat_id, user_id)

    def act_copy_lot_settings(m: Message):
        parts = shlex.split(m.text or "")
        settings = _load_settings()
        if len(parts) < 2:
            msg = ("⚙️ <b>Настройки копирования лота</b>:\n\n" + _fmt_settings(settings) +
                   "\n\n<b>Изменить:</b> /copy_lot_settings translate=auto desc=inline type=autodelivery "
                   "secrets=on game=\"PUBG Mobile\" subcategory=\"Логины\"\n"
                   "Ключи: <b>translate</b> (auto|ru|en|none), <b>desc</b> (source|column|inline), "
                   "<b>type</b> (auto|common|autodelivery), <b>secrets</b> (on|off), <b>game</b>, <b>subcategory</b>.")
            bot.send_message(m.chat.id, msg, parse_mode="HTML")
            return

        for pair in parts[1:]:
            key, _, value = pair.partition("=")
            key = key.strip()
            if key not in DEFAULT_SETTINGS:
                bot.send_message(m.chat.id,
                                 f"❌ Неизвестный ключ «{key}». Допустимо: translate, desc, type, secrets, game, subcategory.")
                return
            try:
                settings[key] = _validate_one(key, value)
            except ValueError as e:
                bot.send_message(m.chat.id, f"❌ {e}")
                return
        _save_settings(settings)
        bot.send_message(m.chat.id, "✅ Настройки сохранены:\n\n" + _fmt_settings(settings), parse_mode="HTML")

    cardinal.add_telegram_commands(UUID, [
        ("copy_lot", "интерактивное копирование лота с редактированием", True),
        ("copy_lot_settings", "настройки копирования (перевод/формат/тип/игра)", True),
    ])

    tg.msg_handler(act_copy_lot, commands=["copy_lot"])
    tg.msg_handler(act_copy_lot_settings, commands=["copy_lot_settings"])
    tg.msg_handler(wait_edit_text, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, CBT_WAIT_EDIT))
    tg.msg_handler(wait_edit_text, func=lambda m: tg.check_state(m.chat.id, m.from_user.id, CBT_WAIT_PRICE))

    tg.cbq_handler(cbq_edit_title, lambda c: c.data == "co.title")
    tg.cbq_handler(cbq_edit_desc, lambda c: c.data == "co.desc")
    tg.cbq_handler(cbq_edit_images, lambda c: c.data == "co.img")
    tg.cbq_handler(cbq_edit_payment, lambda c: c.data == "co.pay")
    tg.cbq_handler(cbq_edit_amount, lambda c: c.data == "co.amt")
    tg.cbq_handler(cbq_edit_secrets, lambda c: c.data == "co.secr")
    tg.cbq_handler(cbq_get_keys, lambda c: c.data == "co.get_keys")
    tg.cbq_handler(cbq_clear_keys, lambda c: c.data == "co.clear_keys")
    tg.cbq_handler(cbq_toggle_auto, lambda c: c.data == "co.ad")
    tg.cbq_handler(cbq_edit_price, lambda c: c.data == "co.price")
    tg.cbq_handler(cbq_edit_subcategory, lambda c: c.data == "co.sub")
    tg.cbq_handler(cbq_select_category, lambda c: c.data.startswith("co.cat:"))
    tg.cbq_handler(cbq_select_subcategory, lambda c: c.data.startswith("co.sub:"))
    tg.cbq_handler(cbq_create, lambda c: c.data == "co.create")
    tg.cbq_handler(cbq_cancel, lambda c: c.data == "co.cancel")
    tg.cbq_handler(cbq_back, lambda c: c.data == "co.back")


BIND_TO_PRE_INIT = [init_commands]
BIND_TO_DELETE = None
