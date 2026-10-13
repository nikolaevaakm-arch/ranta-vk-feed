# Фид VK Рекламы: ЖК Ranta Residence

Каталог недвижимости для VK Рекламы (спецификация Yandex Realty), 1 лот = 1 квартира в свободной продаже.

## Фиды для VK

| Фид | Ссылка | Отбор |
|---|---|---|
| Все квартиры | https://nikolaevaakm-arch.github.io/ranta-vk-feed/vk_feed.xml | свободные квартиры |
| С террасой | https://nikolaevaakm-arch.github.io/ranta-vk-feed/vk_feed_terrace.xml | заполнена площадь террасы (`estate_area_terrace`) |
| С патио | https://nikolaevaakm-arch.github.io/ranta-vk-feed/vk_feed_patio.xml | `estate_customText3 = 1` (1 этаж) |
| Двухуровневые | https://nikolaevaakm-arch.github.io/ranta-vk-feed/vk_feed_duplex.xml | `estate_levelsCount = 2` |

Тематические фиды собираются из уже проверенного основного фида и обновляются только вместе с ним.
Если состав тематического фида меняется, это попадает в сводку в Telegram.

## Как работает

Каждый день в 07:00 МСК GitHub Actions запускает `vk_feed_sync.py`:

1. Скачивает исходные фиды:
   - Macro CRM (JSON): остатки, цены, характеристики;
   - novostroy-spb (XML): брендированные карточки лотов, которые идут главным фото.
2. Собирает новый фид (`build_vk_feed.py`) и проверяет его по требованиям VK.
3. Сверяет источники между собой и с текущим фидом.
4. Обновляет `docs/vk_feed.xml` и делает запись в `sync_log.md`.
5. Если что-то изменилось или есть проблема, отправляет сводку в Telegram.

Фид **не заменяется**, если:
- в новом фиде 0 лотов или меньше половины от текущего;
- он не прошел проверку;
- недоступен Macro.

В этих случаях запуск помечается в Actions как ошибка, а в Telegram уходит сообщение «СТОП».

## Правила фида

- **Цена (`{{product.price}}`):** при 100% оплате или ипотеке (`estate_price_action`, −15%). Условие указано в описании (38-ФЗ).
- **Старая цена (`{{product.old_price}}`):** цена в рассрочку (`estate_price`), передается в тегах `<oldprice>` и `<old_price>`. В справке VK для недвижимости тег старой цены не указан: проверить, что макрос подставляется.
- **Ссылка:** `https://lar-dev.ru/kvartiru/{id Macro}/?utm_content={{banner_id}}&utm_medium=cpc&utm_source=vk_ads&utm_campaign={{campaign_id}}`
- **Главное фото:** карточка лота из novostroy. Если ее нет, берется фото из текущего фида, а если нет и его, то планировка из Macro.
- **custom_label_0–4:** комнатность, корпус, ценовой диапазон, формат (патио, двухуровневая, классическая), отделка.

## Секреты (Settings → Secrets and variables → Actions)

| Имя | Что |
|---|---|
| MACRO_FEED_URL | ссылка на JSON-выгрузку Macro (с токеном) |
| NOVOSTROY_FEED_URL | ссылка на XML novostroy-spb (с токеном) |
| TG_TOKEN | токен Telegram-бота |
| TG_CHAT | id чата для сводок |

## Ручной запуск

- **В облаке:** вкладка Actions → VK feed sync → Run workflow.
- **Локально:**

  ```
  MACRO_FEED_URL=... NOVOSTROY_FEED_URL=... python3 vk_feed_sync.py --feed docs/vk_feed.xml --log sync_log.md --dry-run
  ```

Мастер-копия скриптов лежит в проекте `ranta-marketing/scripts/vk_feed/`.
