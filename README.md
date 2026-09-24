# Ghost Text Prepper

Раз в сутки чистит AI-пометки в черновиках Ghost, пишет короткие SEO/social-описания (≤146 символов) и готовит `.jpg` OG-картинки для Telegram. Когда черновик переводят в **Scheduled**, генерирует обложку через BotHub **Nano Banana 2** (`gemini-3.1-flash-image`) и ставит её в `feature_image` / OG / Twitter.

```bash
python app.py
```

Черновик на выходе без невидимого Unicode (ZWSP, bidi, tag chars) и `data-ai*` — плюс готовый excerpt и `og_image` в `.jpg`, если обложка была PNG. После перехода в Scheduled — BotHub-обложка сразу в `.jpg` без текста на картинке (если задан `BOTHUB_API_KEY`).

## Что делает

| Шаг | Результат |
| --- | --- |
| [watermarks-remover](https://github.com/guillaumemeyer/watermarks-remover) Layer A | С тела и заголовка снимаются невидимые Unicode-пометки и `data-ai*` |
| HF [openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b), fallback [Groq](https://console.groq.com) | `custom_excerpt`, `meta_description`, `og_description`, `twitter_description` |
| [BotHub](https://bothub.ru/text-to-image-ai-generator) Nano Banana 2 (`gemini-3.1-flash-image`) | При `status:scheduled` и `updated_at` в окне прогона: обложка сразу как реальный `.jpg` в `feature_image` / `og_image` / `twitter_image` (под WebpageBot) |
| Telegram OG | PNG-обложка → реальный `.jpg` в `og_image` / `twitter_image` (WebpageBot не любит JPEG под `.png` URL) |

Нужен `HF_TOKEN` и/или `GROQ_API_KEY`. При 402 (credits HF) остаток прогона идёт через Groq. Текст поста не переписывается (Layer B / paraphrase выключен: это ломает тон). C2PA не трогается. Обложки — только если задан `BOTHUB_API_KEY`; посты с уже заполненным `feature_image` пропускаются (`SKIP_COVER_COMPLETE=1`).

## Запуск

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py --self-check
python app.py
```

Только OG-фикс по всем черновикам и published с PNG:

```bash
python app.py --fix-telegram-og
```

Один пост:

```bash
python app.py --fix-telegram-og-slug kak-besplatno-nastroit-sinkhronizatsiiu-obsidian-s-pomoshchiu-livesync
```

## Telegram / WebpageBot

Telegram кэширует превью в момент **первого** запроса. Отложенное сообщение боту со ссылкой на ещё не опубликованный пост в момент отправки часто застаёт PNG-обложку → пустой кэш. `@WebpageBot` потом пишет success, но карточку не показывает; помогает `?v=1` в Saved Messages.

Автофиксы:

| Что | Когда |
| --- | --- |
| Daily prep | черновики + published в окне `lastRunAt` |
| Actions cron `*/30` | посты, обновлённые за последние 2 часа |
| Cloudflare Worker (рекомендуется) | сразу на `post.published` / `post.scheduled` / `post.edited` |

### Worker (мгновенно при публикации)

```bash
cd cloudflare-worker
npx wrangler secret put GHOST_URL          # https://xxx.ghost.io
npx wrangler secret put GHOST_ADMIN_API_KEY
npx wrangler deploy
WEBHOOK_TARGET_URL=https://ghost-telegram-og-webhook.<you>.workers.dev/ \
  python scripts/register-telegram-og-webhooks.py
```

Пока Worker не задеплоен: планируй сообщение в Telegram **минимум на +1 час** после публикации в Ghost (cron чинит OG каждые 30 минут).

`state/last-run.json` — черновики с `updated_at` после `lastRunAt`, плюс scheduled с `updated_at` после `lastRunAt` (для обложек). Свежий baseline ничего не обрабатывает.

Посты с уже заполненным excerpt всё равно чистятся, если в HTML/заголовке есть пометки, или если нужна Telegram OG-картинка.

В том же окне `updated_at` чинятся и **published** посты с PNG-обложкой или многострочным excerpt (`FIX_TELEGRAM_OG=1`) — после генерации BotHub-обложек (сами BotHub-обложки уже заливаются как `.jpg`, этот проход — страховка для старых PNG).

Cron **каждые 30 минут**: workflow **Telegram OG fix (frequent)**.

## Автоматизация

GitHub Actions: cron `0 6 * * *` UTC + `workflow_dispatch`.

Secrets: `GHOST_ADMIN_API_KEY`, `HF_TOKEN`, `GROQ_API_KEY` (fallback), `BOTHUB_API_KEY` (обложки). Variables: `GHOST_URL`, `HF_TEXT_MODEL`, `BOTHUB_IMAGE_MODEL` (публичный URL сайта — не секрет, иначе Job Summary маскирует ссылки).

## Лицензия

[CC BY-NC-SA 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/) — см. [LICENSE](LICENSE).

Некоммерческое использование; производные работы — с тем же лицензированием; указание авторства обязательно.

Layer A Unicode-таблицы — [watermarks-remover](https://github.com/guillaumemeyer/watermarks-remover) (MIT).

## Авторство и поддержка

Код подготовлен с помощью [Cursor](https://cursor.com).

[![Donate](https://img.shields.io/badge/Donate-DonationAlerts-orange)](https://www.donationalerts.com/r/themarfa)
[![Crypto](https://img.shields.io/badge/Crypto-NOWPayments-blue)](https://nowpayments.io/donation/themarfa)

Поддержка проекта:

- [DonationAlerts](https://www.donationalerts.com/r/themarfa)
- [Донат криптой (NOWPayments)](https://nowpayments.io/donation/themarfa)
