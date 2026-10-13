"""Фид Macro CRM (JSON, выгрузка для сайта) -> каталог VK Рекламы (спецификация Yandex Realty).

Спецификация: https://ads.vk.ru/help/articles/ecomm_catalog_y_estate
Используется агентом vk_feed_sync.py; можно запускать и вручную:
  python3 build_vk_feed.py --out путь.xml [--src URL|файл] [--card-src URL|файл] [--price base|action] [--image card|plan]
Адреса исходных фидов содержат токены, поэтому в коде их нет: env MACRO_FEED_URL и NOVOSTROY_FEED_URL
или файл ~/.config/ranta-mcp/vk_feed.env (строки KEY=VALUE).

v0.6 от 2026-10-13: старая цена (estate_price, в рассрочку) в <oldprice> и <old_price> для {{product.old_price}};
  в справке VK для недвижимости тег не указан, oldprice взят из спецификации товаров Яндекса.
v0.5 от 2026-10-13: тематические фиды SEGMENTS (террасы, патио, двухуровневые); терраса в описании.
v0.4 от 2026-10-13: цена при 100% оплате или ипотеке (estate_price_action, -15%) с условием в описании;
UTM с макросами VK; фото из прошлого фида, если в novostroy лота нет.
Прошлые версии: archive/2026-10-13_build_vk_feed_novostroy_v01.py
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

NS = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"
N = "{%s}" % NS
ENV_FILE = os.path.expanduser("~/.config/ranta-mcp/vk_feed.env")

# id лота в Macro = id страницы квартиры на сайте.
# Макросы VK {{banner_id}} и {{campaign_id}} нельзя кодировать, поэтому строку не прогоняем через urlencode
LANDING = "https://lar-dev.ru/kvartiru/{id}/"
UTM = "utm_content={{banner_id}}&utm_medium=cpc&utm_source=vk_ads&utm_campaign={{campaign_id}}"

PRICE_NOTE = {"action": "Цена при 100% оплате или ипотеке.", "base": "Цена при покупке в рассрочку."}

DESC_MAX = 256
LABEL_MAX = 256


def source_url(name):
    if os.environ.get(name):
        return os.environ[name]
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k == name and v:
                return v
    sys.exit(f"Не задан {name}: env или {ENV_FILE}")


def fetch(src, timeout=180, tries=3):
    if not src.startswith("http"):
        return open(src, "rb").read()
    err = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:  # сеть, 5xx
            err = e
    raise RuntimeError(f"не удалось скачать фид: {err}")


def fnum(x):
    """39.02 -> '39,02', 3.0 -> '3'"""
    s = ("%.2f" % float(x)).rstrip("0").rstrip(".")
    return s.replace(".", ",")


def price_bucket(p):
    m = p / 1e6
    if m < 20:
        return "до 20 млн"
    if m < 30:
        return "20-30 млн"
    if m < 40:
        return "30-40 млн"
    return "от 40 млн"


def is_duplex(x):
    return (x.get("estate_levelsCount") or 1) > 1


def is_patio(x):
    # customText3 = 1: патио (все на 1 этаже, в описании лота «В планировке предусмотрены: патио»)
    return x.get("estate_customText3") == "1"


def has_terrace(x):
    # площадь террасы заполнена только у лотов, где терраса есть в планировке
    return bool(x.get("estate_area_terrace"))


# Тематические фиды: имя файла -> отбор лотов
SEGMENTS = {
    "vk_feed_terrace.xml": has_terrace,
    "vk_feed_patio.xml": is_patio,
    "vk_feed_duplex.xml": is_duplex,
}


def lot_format(x):
    if is_duplex(x):
        return "Двухуровневая"
    if is_patio(x):
        return "С патио"
    if has_terrace(x):
        return "С террасой"
    return "Классическая"


def window_view(x):
    v = (x.get("estate_windowView") or "").lower()
    if "лес" in v:
        return "вид на лес"
    if "двор" in v:
        return "окна во двор"
    return ""


def section(x):
    return (x.get("house_title") or "").replace("Корпус", "").strip()


def build_desc(x, price_mode):
    lot = (f"{x['estate_rooms']}-комнатная квартира {fnum(x['estate_area'])} м², "
           f"{x['estate_floor']} этаж из {x['estate_floors_in_house']}, корпус {section(x)}.")
    extra = []
    fmt = lot_format(x)
    if fmt in ("Двухуровневая", "С патио"):
        extra.append(fmt.lower())
    if has_terrace(x):
        extra.append(f"терраса {fnum(x['estate_area_terrace'])} м²")
    extra.append(f"{(x.get('estate_restoration') or '').lower()} отделка")
    if window_view(x):
        extra.append(window_view(x))
    # У двухуровневых в Macro 3 м, а на карточке и в novostroy 5 м (второй свет): не пишем
    if x.get("estate_ceilingHeight_flat") and fmt != "Двухуровневая":
        extra.append(f"потолки {fnum(x['estate_ceilingHeight_flat'])} м")
    s = ", ".join(e for e in extra if e.strip())
    # Условие цены обязательно (38-ФЗ), остальное добавляем, пока влезает в лимит
    parts = [lot, s[0].upper() + s[1:] + ".", PRICE_NOTE[price_mode]]
    for opt in ("Малоэтажный ЖК бизнес-класса в сосновом лесу Зеленогорска.",
                f"Срок сдачи: {x.get('estate_inServiceQuartal_human') or x.get('estate_inServiceYear')}."):
        if len(" ".join(parts + [opt])) <= DESC_MAX:
            parts.append(opt)
    return " ".join(parts)[:DESC_MAX]


def sub(parent, tag, text=None):
    e = ET.SubElement(parent, N + tag)
    if text is not None:
        e.text = str(text)
    return e


def lot_key(section_, floor, area, price):
    return (section_, str(floor), "%.2f" % float(area), str(round(float(price))))


def parse_novostroy(raw):
    """Лоты фида novostroy-spb: {(корпус, этаж, площадь, базовая цена): [dict]}.
    Одинаковые лоты лежат списком и раздаются по очереди (карточки у них тоже одинаковые)."""
    lots = {}
    for o in ET.fromstring(raw).findall(N + "offer"):
        key = lot_key(o.findtext(N + "building-section"), o.findtext(N + "floor"),
                      o.findtext(f"{N}area/{N}value"), o.findtext(f"{N}price/{N}value"))
        lots.setdefault(key, []).append({
            "id": o.get("internal-id"),
            "card": next((i.text for i in o.findall(N + "image") if i.get("tag") == "plan"), None),
            "final_price": o.findtext(f"{N}discount/{N}final-price"),
            "discount_end": o.findtext(f"{N}discount/{N}end-date"),
        })
    return lots


def is_active(x):
    return (x.get("category") == "flat" and x.get("activity") == "sell"
            and x.get("is_published") == 1 and not x.get("is_sold") and not x.get("is_reserved"))


def lot_price(x, price_mode):
    if price_mode == "action" and x.get("estate_price_action"):
        return round(float(x["estate_price_action"])), "action"
    return round(float(x["estate_price"])), "base"


def convert(lots, price_mode="action", novostroy=None, prev_images=None):
    """novostroy: результат parse_novostroy или None (тогда фото = планировка Macro).
    prev_images: {id: url} из текущего фида VK, запасной вариант, если в novostroy лота нет."""
    ET.register_namespace("", NS)
    prev_images = prev_images or {}
    novostroy = {k: list(v) for k, v in (novostroy or {}).items()}
    out = ET.Element(N + "realty-feed")
    sub(out, "generation-date", dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    stats = {"offers": 0, "skipped": [], "no_card": [], "base_price": [], "matched": {}}
    for x in filter(is_active, lots):
        oid = str(x["id"])
        price, mode = lot_price(x, price_mode)
        if mode != price_mode:
            stats["base_price"].append(oid)
        img = x.get("title_image") or x.get("plan_image")
        nl = (novostroy.get(lot_key(section(x), x["estate_floor"], x["estate_area"], x["estate_price"]))
              or [None]).pop(0)
        if nl:
            stats["matched"][oid] = nl
        if nl and nl["card"]:
            img = nl["card"]
        elif prev_images.get(oid):
            img = prev_images[oid]
        else:
            stats["no_card"].append(oid)
        if not (price and img):
            stats["skipped"].append(oid)
            continue
        rooms, area = x["estate_rooms"], x["estate_area"]

        o = sub(out, "offer")
        o.set("internal-id", oid)
        sub(o, "type", "продажа")
        sub(o, "property-type", "жилая")
        sub(o, "category", "квартира")
        # building-name = заголовок карточки в VK, поэтому делаем его уникальным для лота
        sub(o, "building-name", f"{rooms}-комн. квартира {fnum(area)} м², Ранта Резиденс")
        sub(o, "organization", "LAR Development")

        loc = sub(o, "location")
        sub(loc, "country", "Россия")
        # В Macro регион «Ленинградская область», это ошибка: Зеленогорск входит в СПб
        sub(loc, "region", "Санкт-Петербург")
        sub(loc, "locality-name", "Санкт-Петербург")
        sub(loc, "sub-locality-name", "Курортный район")
        sub(loc, "address", f"Зеленогорск, Кривоносовская улица, 57, корпус {section(x)}")

        p = sub(o, "price")
        sub(p, "value", price)
        sub(p, "currency", "RUB")
        base = round(float(x["estate_price"]))
        if mode == "action" and base > price:
            # {{product.price}} = цена при 100% оплате, {{product.old_price}} = цена в рассрочку
            sub(o, "oldprice", base)
            sub(o, "old_price", base)
        sub(o, "url", LANDING.format(id=oid) + "?" + UTM)
        sub(o, "image", img)

        a = sub(o, "area")
        sub(a, "value", round(float(area)))
        sub(a, "unit", "кв. м")
        sub(o, "rooms", rooms)
        sub(o, "floor", x["estate_floor"])
        sub(o, "floors_total", x["estate_floors_in_house"])
        sub(o, "floors", x["estate_floors_in_house"])
        sub(o, "built-year", x.get("estate_inServiceYear"))
        sub(o, "description", build_desc(x, mode))

        labels = [
            f"{rooms}-комнатная",
            f"Корпус {section(x)}",
            price_bucket(price),
            lot_format(x),
            f"{x.get('estate_restoration')} отделка",
        ]
        for i, lab in enumerate(labels):
            sub(o, f"custom_label_{i}", lab[:LABEL_MAX])
        stats["offers"] += 1
    ET.indent(out)
    return out, stats


def subset(root, ids):
    """Копия фида только с лотами из ids"""
    out = ET.Element(N + "realty-feed")
    for el in root:
        if el.tag != N + "offer" or el.get("internal-id") in ids:
            out.append(el)
    return out


def segment_ids(lots, pred):
    return {str(x["id"]) for x in lots if is_active(x) and pred(x)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="JSON Macro: URL или файл (по умолчанию MACRO_FEED_URL)")
    ap.add_argument("--card-src", help="XML novostroy: URL или файл (по умолчанию NOVOSTROY_FEED_URL)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--price", choices=["base", "action"], default="action",
                    help="action = при 100%% оплате или ипотеке (-15%%), base = в рассрочку")
    ap.add_argument("--image", choices=["card", "plan"], default="card",
                    help="card = карточка из фида novostroy-spb, plan = планировка из Macro")
    args = ap.parse_args()
    lots = json.loads(fetch(args.src or source_url("MACRO_FEED_URL")))
    nov = parse_novostroy(fetch(args.card_src or source_url("NOVOSTROY_FEED_URL"))) \
        if args.image == "card" else None
    out, stats = convert(lots, args.price, nov)
    ET.ElementTree(out).write(args.out, encoding="UTF-8", xml_declaration=True)
    print(f"offers: {stats['offers']}, skipped: {stats['skipped']}, "
          f"без карточки: {len(stats['no_card'])}, базовая цена вместо акционной: {len(stats['base_price'])}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
