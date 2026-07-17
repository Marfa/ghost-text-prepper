# Ghost Text Prepper

Раз в сутки обрабатывает **новые** черновики Ghost: краткий excerpt (≤146) через Hugging Face и feature image через Bonsai.

## Текст (HF)

**[Qwen/Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)** — только для `custom_excerpt` / meta / og / twitter description. Нужен `HF_TOKEN`.

## Картинки (Bonsai)

[prism-ml/Bonsai-Image-Demo](https://huggingface.co/spaces/prism-ml/Bonsai-Image-Demo): Ternary, `1248×832`, seed `42`, steps `4`. Промпт строится из заголовка (без второго LLM-вызова).

## Запуск

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py --self-check
python app.py
```

## Инкрементальный режим

`state/last-run.json` хранит `lastRunAt`. Берутся только черновики с `updated_at` **после** этой метки. Первый прогон / свежий baseline ничего не обрабатывает — только фиксирует точку отсчёта.

## Автоматизация

GitHub Actions: cron `0 6 * * *` UTC + `workflow_dispatch`.

Secrets: `GHOST_URL`, `GHOST_ADMIN_API_KEY`, `HF_TOKEN`.
