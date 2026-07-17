# Ghost Text Prepper

Раз в сутки собирает черновики Ghost, пишет короткие описания (≤146 символов) и генерирует feature image через Hugging Face.

## Модель для текста

**[Qwen/Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)** через [Inference Providers](https://huggingface.co/docs/api-inference) (`huggingface_hub.InferenceClient`).

Почему она:

- доступна онлайн по API (без локальной GPU);
- мультиязычная (в т.ч. русский);
- один вызов — и excerpt, и промпт для картинки с ограничением «без текста на изображении».

Нужен `HF_TOKEN` с доступом к Inference Providers.

## Картинки

Space [prism-ml/Bonsai-Image-Demo](https://huggingface.co/spaces/prism-ml/Bonsai-Image-Demo) — Docker/FastAPI, не Gradio.

`POST {BONSAI_URL}/generate` с параметрами как в UI:

| Параметр | Значение |
| --- | --- |
| backend | `bonsai-ternary-gemlite` (Bonsai · Ternary) |
| width × height | `1248 × 832` (3:2) |
| seed | `42` |
| steps | `4` |
| mode | single (`/generate`, не `/generate/compare`) |

PNG загружается в Ghost Admin API, URL пишется в `feature_image`, `og_image`, `twitter_image`.

Текст пишется в `custom_excerpt`, `meta_description`, `og_description`, `twitter_description`.

## Запуск

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить GHOST_*, HF_TOKEN
python app.py --self-check
python app.py
```

Пайплайн по образцу [ghost_translator](https://github.com/Marfa/ghost_translator) (Admin JWT + httpx) и [ghost_email](https://github.com/Marfa/ghost_email) (загрузка картинки + суточный/недельный cron).

## Автоматизация

GitHub Actions: `.github/workflows/prep.yml` — cron `0 6 * * *` (06:00 UTC) и `workflow_dispatch`.

Secrets: `GHOST_URL`, `GHOST_ADMIN_API_KEY`, `HF_TOKEN`. Опционально `BONSAI_TOKEN`.

Черновики с уже заполненными `custom_excerpt` и `feature_image` пропускаются (`SKIP_COMPLETE=1`).

## Инкрементальный режим

Скрипт хранит время последнего успешного запуска в `state/last-run.json` и обрабатывает только черновики с `updated_at` **после** этой метки (новые черновики и посты, переведённые в draft). При ошибках метка не сдвигается — те же посты попадут в следующий прогон.

Первый запуск без state-файла только создаёт baseline и ничего не трогает.

Чтобы прогнать старые черновики вручную — удалите или откатите `state/last-run.json`.

## Замечания

- Bonsai Space — общий демо-ресурс; между постами есть пауза 2 с. При высокой нагрузке генерация может очередиться или отвалиться.
- Если Space начнёт требовать Bearer — задайте `BONSAI_TOKEN`.
