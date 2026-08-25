# Ghost Text Prepper

Раз в сутки чистит AI-пометки в черновиках Ghost и пишет короткие SEO/social-описания (≤146 символов) через Hugging Face.

```bash
python app.py
```

Черновик на выходе без невидимого Unicode (ZWSP, bidi, tag chars) и `data-ai*` — плюс готовый excerpt.

## Что делает

| Шаг | Результат |
| --- | --- |
| [watermarks-remover](https://github.com/guillaumemeyer/watermarks-remover) Layer A | С тела и заголовка снимаются невидимые Unicode-пометки и `data-ai*` |
| [openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b) (Inference Providers) | `custom_excerpt`, `meta_description`, `og_description`, `twitter_description` |

Нужен `HF_TOKEN`. Текст поста не переписывается (Layer B / paraphrase выключен: это ломает тон). Картинки и C2PA не трогаются.

## Запуск

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py --self-check
python app.py
```

## Инкрементальный режим

`state/last-run.json` — только черновики с `updated_at` после `lastRunAt`. Свежий baseline ничего не обрабатывает.

Посты с уже заполненным excerpt всё равно чистятся, если в HTML/заголовке есть пометки.

## Автоматизация

GitHub Actions: cron `0 6 * * *` UTC + `workflow_dispatch`.

Secrets: `GHOST_ADMIN_API_KEY`, `HF_TOKEN`. Variables: `GHOST_URL`, `HF_TEXT_MODEL` (публичный URL сайта — не секрет, иначе Job Summary маскирует ссылки).

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
