# Состояние реализации

## Подготовлено

- Backend FastAPI: общий исполнитель запросов, 120 воспроизводимых записей, 15 экспериментальных задач и отдельная тренировка.
- SQLite: миграции, постоянное хранение, проверка неизменности протокола/эталонов, транзакции и идемпотентные попытки.
- Доступ: индивидуальные многоразовые приглашения, HttpOnly-cookie, роли `participant`/`researcher`, владение ресурсами и отзыв приглашения без удаления прогресса.
- M1 frontend: последовательные фильтры, preview, таблица, группировка/агрегат/экстремум, сортировка, CSV и итоговая проверка.
- M2 frontend: адаптированный `TagInput`, подсказки + ручной структурированный ввод, единая кнопка «Выполнить» и backend-компиляция в общий `Query`.
- Переиспользованы `TagInput`, `LiveSearchProgress`, `ConfirmDialog`, `AppToast`; табличная часть адаптирована из `Corpus` без зависимостей старого проекта.
- Frontend на Bun + Svelte 5, публичный порт `3033`; same-origin proxy к внутреннему backend.
- Task Mining frontend: ввод, pointer/scroll, focus, navigation, request start/finish; серверный курсор сохраняет sequence/offset при reload активной пробы.
- Исследовательский экран: только Excel/CSV-выгрузки, без отдельной панели аналитики.
- M3–M5 явно недоступны до выбора систем интерпретации; подмена интерфейсом M2 отсутствует.

## Проверено в текущей среде

- `python -m pytest -q -p no:cacheprovider` — **59 passed** после frontend-контрактов.
- Чистый TypeScript-слой `api.ts`, `event-logger.ts`, `trial-actions.ts`, `types.ts` — `tsc --noEmit --strict` без ошибок.
- `server.ts` и `vite.config.ts` — TypeScript parse/noCheck без ошибок.
- Backend-тесты дополнительно проверяют повторный вход, восстановление прогресса, researcher Excel, все 15 M2-задач и продолжение event sequence/offset после ранее сохранённых событий.

## Граница проверки

Docker/Bun в текущей рабочей среде недоступны, сеть пакетного менеджера также недоступна. По правилам репозитория агент не собирает Docker-образы без явного запроса пользователя. Поэтому полный `bun run check`/Vite build здесь не запускался; он встроен как обязательный gate в build-stage `frontend/Dockerfile`.

Секрет `INVITATION_SIGNING_KEY` агент не создавал, не читал и не записывал. Пользователь создаёт корневой `.env` самостоятельно.

M3–M5 не исполняются до выбора систем интерпретации. Полная пятиблочная сессия пока невозможна.

## Пользовательская проверка после применения патча

```sh
docker compose build backend frontend
docker compose up -d --no-build
docker compose exec backend python -m pytest -q -p no:cacheprovider
docker compose exec backend python tests/smoke_server.py
docker compose exec backend python -m pip check
```

Затем:

```sh
docker compose exec backend sh scripts/invite.sh P001
docker compose exec backend sh scripts/invite.sh RESEARCHER --role researcher
```

Проверить участника через персональную ссылку на `http://localhost:3033`: создание/возобновление сессии, M1 preview + проверку, M2 TagInput + «Выполнить», повторный вход с сохранённым прогрессом. Через ссылку исследователя проверить кнопки Excel/CSV ZIP.
