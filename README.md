# Ghost Text Prepper

Раз в сутки пишет короткие SEO/social-описания (≤146 символов) для **новых** черновиков Ghost через Hugging Face.

## Текст

**[Qwen/Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)** → `custom_excerpt`, `meta_description`, `og_description`, `twitter_description`.

Нужен `HF_TOKEN`.

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

## Автоматизация

GitHub Actions: cron `0 6 * * *` UTC + `workflow_dispatch`.

Secrets: `GHOST_URL`, `GHOST_ADMIN_API_KEY`, `HF_TOKEN`.

## Лицензия

[CC BY-NC-SA 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/) — см. [LICENSE](LICENSE).

Некоммерческое использование; производные работы — с тем же лицензированием; указание авторства обязательно.

## Авторство и поддержка

Код подготовлен с помощью [Cursor](https://cursor.com).

[![Donate](https://img.shields.io/badge/Donate-DonationAlerts-orange)](https://www.donationalerts.com/r/themarfa)
[![Crypto](https://img.shields.io/badge/Crypto-NOWPayments-blue)](https://nowpayments.io/donation/themarfa)

Поддержка проекта:

- [DonationAlerts](https://www.donationalerts.com/r/themarfa)
- [Донат криптой (NOWPayments)](https://nowpayments.io/donation/themarfa)
