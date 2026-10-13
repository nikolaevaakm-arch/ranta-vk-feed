"""Ежедневная актуализация фида VK Рекламы по исходным фидам Macro CRM и novostroy-spb.

Что делает:
1. Скачивает Macro (JSON, остатки и цены) и novostroy-spb (XML, карточки-фото), 3 попытки на каждый.
2. Собирает новый фид (build_vk_feed.convert) и проверяет его по требованиям VK.
3. Сверяет источники между собой: лоты без пары, расхождение цен, окончание скидки в novostroy.
4. Сравнивает с текущим фидом VK: новые и ушедшие лоты, изменения цен, фото, описаний.
5. Предохранители: если лотов 0 или меньше --min-ratio от текущего, или фид не прошел проверку,
   файл не заменяется, код выхода 2.
6. Рядом с основным фидом пишет тематические (build_vk_feed.SEGMENTS): террасы, патио,
   двухуровневые. Они собираются из проверенного основного и обновляются только вместе с ним.
7. Пишет запись в журнал (--log) и отправляет сводку в Telegram (если заданы TG_TOKEN и TG_CHAT
   или файлы ~/.config/ranta-mcp/telegram_bot_token и telegram_chat_id).

Запуск: python3 vk_feed_sync.py --feed путь/vk_feed.xml --log путь/sync_log.md [--dry-run] [--force]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_vk_feed as bf  # noqa: E402

N = bf.N
CFG = os.path.expanduser("~/.config/ranta-mcp")
REQUIRED = ["type", "location/address", "location/locality-name", "location/region",
            "location/country", "price/value", "price/currency", "url", "image"]


def read_feed(path):
    """{id: {price, image, desc, title}} из текущего фида VK"""
    if not os.path.exists(path):
        return {}
    res = {}
    for o in ET.parse(path).getroot().findall(N + "offer"):
        res[o.get("internal-id")] = {
            "price": int(o.findtext(f"{N}price/{N}value")),
            "image": o.findtext(N + "image"),
            "desc": o.findtext(N + "description"),
            "title": o.findtext(N + "building-name"),
        }
    return res


def feed_dict(root):
    tmp = ET.ElementTree(root)
    return {o.get("internal-id"): {
        "price": int(o.findtext(f"{N}price/{N}value")),
        "image": o.findtext(N + "image"),
        "desc": o.findtext(N + "description"),
        "title": o.findtext(N + "building-name"),
    } for o in tmp.getroot().findall(N + "offer")}


def validate(root):
    errors = []
    ids = set()
    for o in root.findall(N + "offer"):
        oid = o.get("internal-id") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", oid) or oid in ids:
            errors.append(f"{oid}: плохой или повторный id")
        ids.add(oid)
        for r in REQUIRED:
            if not (o.findtext("/".join(N + p for p in r.split("/"))) or "").strip():
                errors.append(f"{oid}: пусто {r}")
        if int(o.findtext(f"{N}price/{N}value") or 0) <= 0:
            errors.append(f"{oid}: цена <= 0")
        if len(o.findtext(N + "description") or "") > bf.DESC_MAX:
            errors.append(f"{oid}: описание длиннее {bf.DESC_MAX}")
        if not (o.findtext(N + "url") or "").startswith("https://lar-dev.ru/"):
            errors.append(f"{oid}: ссылка не на lar-dev.ru")
    return errors


def rub(v):
    return f"{v:,}".replace(",", " ")


def tg_creds():
    tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not (tok and chat):
        try:
            tok = open(os.path.join(CFG, "telegram_bot_token")).read().strip()
            chat = open(os.path.join(CFG, "telegram_chat_id")).read().strip()
        except OSError:
            return None, None
    return tok, chat


def send_tg(text):
    tok, chat = tg_creds()
    if not tok:
        return "telegram не настроен"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000]}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30)
        return "отправлено в telegram"
    except Exception as e:
        return f"telegram: ошибка {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", required=True, help="опубликованный фид VK, читается и перезаписывается")
    ap.add_argument("--log", required=True, help="журнал синхронизаций, markdown, запись добавляется сверху")
    ap.add_argument("--min-ratio", type=float, default=0.5)
    ap.add_argument("--force", action="store_true", help="записать фид, даже если сработал предохранитель")
    ap.add_argument("--dry-run", action="store_true", help="не писать фид и не слать в telegram")
    ap.add_argument("--macro-src")
    ap.add_argument("--novostroy-src")
    args = ap.parse_args()

    now = dt.datetime.now().astimezone()
    lines, alerts = [], []
    cur = read_feed(args.feed)

    try:
        lots = json.loads(bf.fetch(args.macro_src or bf.source_url("MACRO_FEED_URL")))
    except Exception as e:
        msg = f"VK-фид Ранта {now:%d.%m %H:%M}: СТОП, Macro недоступен ({e}). Фид не обновлен."
        print(msg, file=sys.stderr)
        if not args.dry_run:
            send_tg(msg)
        sys.exit(2)
    try:
        nov = bf.parse_novostroy(bf.fetch(args.novostroy_src or bf.source_url("NOVOSTROY_FEED_URL")))
    except Exception as e:
        nov = None
        alerts.append(f"novostroy недоступен ({e}): фото взяты из текущего фида")

    prev_images = {k: v["image"] for k, v in cur.items()}
    root, st = bf.convert(lots, "action", nov, prev_images)
    new = feed_dict(root)

    # Сверка источников
    if nov is not None:
        nov_all = [n for v in nov.values() for n in v]
        matched_ids = {n["id"] for n in st["matched"].values()}
        orphan_nov = [n["id"] for n in nov_all if n["id"] not in matched_ids]
        if orphan_nov:
            alerts.append(f"в novostroy {len(orphan_nov)} лотов, которых нет среди свободных в Macro "
                          f"(проданы или изменились цена/площадь): {', '.join(orphan_nov[:10])}")
        if st["no_card"]:
            alerts.append(f"нет карточки в novostroy у {len(st['no_card'])} лотов Macro "
                          f"(стоит планировка): {', '.join(st['no_card'][:10])}")
        price_mismatch = []
        for oid, n in st["matched"].items():
            if n["final_price"] and oid in new and abs(int(n["final_price"]) - new[oid]["price"]) > 1:
                price_mismatch.append(f"{oid}: VK {rub(new[oid]['price'])} / novostroy {rub(int(n['final_price']))}")
        if price_mismatch:
            alerts.append(f"цена со скидкой в Macro и novostroy расходится у {len(price_mismatch)} лотов: "
                          + "; ".join(price_mismatch[:5]))
        ends = {n["discount_end"][:10] for n in nov_all if n.get("discount_end")}
        for e in sorted(ends):
            days = (dt.date.fromisoformat(e) - now.date()).days
            if days <= 3:
                alerts.append(f"скидка в novostroy действует до {e} (осталось {days} дн.): "
                              f"проверить, сохраняется ли цена при 100% оплате на сайте")
    if st["base_price"]:
        alerts.append(f"нет цены при 100% оплате у {len(st['base_price'])} лотов, стоит цена в рассрочку: "
                      f"{', '.join(st['base_price'][:10])}")

    # Изменения относительно текущего фида
    added = sorted(set(new) - set(cur))
    removed = sorted(set(cur) - set(new))
    both = set(new) & set(cur)
    price_ch = sorted((k for k in both if new[k]["price"] != cur[k]["price"]),
                      key=lambda k: abs(new[k]["price"] - cur[k]["price"]), reverse=True)
    # novostroy перегенерирует карточки и меняет ?v=... в ссылке; изменением фото считаем смену файла
    img_ch = [k for k in both if new[k]["image"].split("?")[0] != cur[k]["image"].split("?")[0]]
    desc_ch = [k for k in both if new[k]["desc"] != cur[k]["desc"] or new[k]["title"] != cur[k]["title"]]

    # Предохранители
    errors = validate(root)
    stop = []
    if st["offers"] == 0:
        stop.append("в новом фиде 0 лотов")
    if cur and st["offers"] < args.min_ratio * len(cur):
        stop.append(f"лотов {st['offers']} против {len(cur)} в текущем фиде (< {args.min_ratio:.0%})")
    if errors:
        stop.append(f"{len(errors)} ошибок проверки: " + "; ".join(errors[:5]))

    status = "СТОП, фид не обновлен" if stop and not args.force else "обновлен"
    if args.dry_run:
        status = "проверка без записи (dry-run)"
    seg_lines = []
    for fname, pred in bf.SEGMENTS.items():
        ids = bf.segment_ids(lots, pred) & set(new)
        path = os.path.join(os.path.dirname(os.path.abspath(args.feed)), fname)
        before = set(read_feed(path))
        seg_lines.append(f"{fname}: {len(ids)}" + (f" (было {len(before)})" if before != ids else ""))
        if not ids:
            alerts.append(f"{fname}: 0 лотов, VK может отклонить пустой каталог")
        if not args.dry_run and (not stop or args.force):
            tmp = path + ".tmp"
            ET.ElementTree(bf.subset(root, ids)).write(tmp, encoding="UTF-8", xml_declaration=True)
            os.replace(tmp, path)
        if before and ids != before:
            added_s, removed_s = sorted(ids - before), sorted(before - ids)
            if added_s:
                seg_lines[-1] += ", +" + ", ".join(added_s)
            if removed_s:
                seg_lines[-1] += ", −" + ", ".join(removed_s)
    if not args.dry_run and (not stop or args.force):
        tmp = args.feed + ".tmp"
        ET.ElementTree(root).write(tmp, encoding="UTF-8", xml_declaration=True)
        os.replace(tmp, args.feed)

    lines.append(f"VK-фид Ранта {now:%d.%m.%Y %H:%M}: {status}")
    lines.append(f"Лотов: {st['offers']} (было {len(cur)}). Новые: {len(added)}, ушли: {len(removed)}, "
                 f"цена изменилась: {len(price_ch)}, фото: {len(img_ch)}, текст: {len(desc_ch)}")
    lines.append("Тематические фиды: " + "; ".join(seg_lines))
    if added:
        lines.append("Новые: " + ", ".join(f"{k} ({new[k]['title']}, {rub(new[k]['price'])} ₽)" for k in added[:10]))
    if removed:
        lines.append("Ушли из фида: " + ", ".join(f"{k} ({cur[k]['title']})" for k in removed[:10]))
    if price_ch:
        lines.append("Цены: " + "; ".join(f"{k} {rub(cur[k]['price'])} → {rub(new[k]['price'])}" for k in price_ch[:10]))
    for s in stop:
        lines.append("СТОП: " + s)
    for a in alerts:
        lines.append("Внимание: " + a)
    report = "\n".join(lines)
    print(report)

    if not args.dry_run:
        old = open(args.log, encoding="utf-8").read() if os.path.exists(args.log) else "# Журнал синхронизации фида VK\n"
        head, _, body = old.partition("\n")
        entry = "\n## " + lines[0] + "\n" + "\n".join("- " + s for s in lines[1:]) + "\n"
        with open(args.log, "w", encoding="utf-8") as f:
            f.write(head + "\n" + entry + body)
        # В telegram пишем, только если что-то изменилось или есть проблемы
        if added or removed or price_ch or img_ch or stop or alerts or any("было" in x for x in seg_lines):
            print(send_tg(report), file=sys.stderr)
    sys.exit(2 if stop and not args.force else 0)


if __name__ == "__main__":
    main()
