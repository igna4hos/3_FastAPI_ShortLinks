# 3 проект - Fast API. Колесников Игнат Евгеньевич

API-сервис для сокращения ссылок на FastAPI. Приложение можно попробовать самому по ссылке [ссылке](https://three-fastapi-shortlinks.onrender.com/docs).

Сервис позволяет:

- регистрировать и авторизовывать пользователей;
- создавать короткие ссылки;
- задавать время жизни ссылки;
- задавать лимит переходов;
- редактировать и удалять свои ссылки;
- получать статистику по ссылке;
- искать ссылки по оригинальному URL;
- смотреть историю удалённых/просроченных ссылок;
- получать список популярных ссылок;
- выполнять редирект по короткому коду.

### Стек

- FastAPI
- PostgreSQL
- SQLAlchemy Async
- Redis
- JWT-аутентификация

<img width="1552" height="927" alt="Screenshot 2026-03-09 at 22 11 01" src="https://github.com/user-attachments/assets/fabb1aa0-bcc5-4c43-bb46-a3312fee60a1" />


## Описание API
### 1. Регистрация
POST /auth/register

Создаёт нового пользователя.

Тело запроса
```
{
  "email": "user@example.com",
  "password": "stringst"
}
```
Ответ
```
{
  "id": 1,
  "email": "user@example.com",
  "created_at": "2026-03-09T12:00:00Z"
}
```

### 2. Авторизация

POST /auth/login

Проверяет email и пароль и возвращает JWT access token.

Тело запроса
```
{
  "email": "user@example.com",
  "password": "string"
}
```
Ответ
```
{
  "access_token": "string",
  "token_type": "bearer"
}
```

### 3. Создание короткой ссылки

POST /links/shorten

Создаёт короткую ссылку.

Поддерживает:

- кастомный alias через custom_alias;
- срок жизни через expires_at;
- лимит переходов через click_limit. Если ничего не передавать в теле запроса, то не будет ограничений на количество переходов по ссылке.

Если пользователь авторизован, ссылка будет привязана к его аккаунту.

Тело запроса
```
{
  "original_url": "https://example.com/",
  "custom_alias": "string",
  "expires_at": "2026-03-09T20:08:12.132Z",
  "click_limit": 1
}
```
Ответ
```
{
  "short_code": "string",
  "short_url": "string",
  "original_url": "string",
  "created_at": "2026-03-09T20:08:30.874Z",
  "expires_at": "2026-03-09T20:08:30.874Z",
  "click_limit": 0
}
```

Пример реального ответа
```
{
  "short_code": "peaky",
  "short_url": "https://three-fastapi-shortlinks.onrender.com/peaky",
  "original_url": "https://www.kinopoisk.ru/film/5461947/?utm_referrer=organic.kinopoisk.ru",
  "created_at": "2026-03-09T20:49:44.884592Z",
  "expires_at": "2026-06-20T20:12:00Z",
  "click_limit": 1000
}
```
Когда мы переходим по ссылке с поля [short_url](https://three-fastapi-shortlinks.onrender.com/peaky), то переходим на оригинальный сайт

### 4. Поиск ссылки по оригинальному URL

GET /links/search?original_url={url}

Возвращает список активных ссылок, созданных для указанного оригинального URL

### 5. Получение статистики по ссылке

GET /links/{short_code}/stats

Возвращает статистику по активной ссылке:

- short code;
- оригинальный URL;
- дату создания;
- количество переходов;
- дату последнего использования;
- дату истечения;
- лимит переходов;
- признак того, что ссылка была создана авторизованным пользователем.

### 6. Обновление ссылки

PUT /links/{short_code}

Доступно только авторизованному пользователю и только для своих ссылок.
Можно изменить:

- original_url
- expires_at
- click_limit

### 7. Удаление ссылки

DELETE /links/{short_code}

Доступно только авторизованному пользователю и только для своих ссылок.

## Дополнительный функционал

### 8. История удалённых и истёкших ссылок

GET /links/expired/history

Возвращает список архивных записей из таблицы expired_links с указанием причины удаления.

Реальный пример ответа
```
[
  {
    "short_code": "original",
    "original_url": "https://inoriginal.net/films/page/4/",
    "created_at": "2026-03-09T19:30:02.411139Z",
    "expired_at": "2026-03-09T19:30:58.152652Z",
    "last_used_at": "2026-03-09T19:30:27.777516Z",
    "click_count": 5,
    "click_limit": null,
    "expiration_reason": "manual_delete"
  },
  {
    "short_code": "openplease",
    "original_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1",
    "created_at": "2026-03-09T19:21:26.962362Z",
    "expired_at": "2026-03-09T19:21:54.499346Z",
    "last_used_at": "2026-03-09T19:21:54.499301Z",
    "click_count": 5,
    "click_limit": 5,
    "expiration_reason": "click_limit_reached"
  },
  {
    "short_code": "json",
    "original_url": "https://jsonformatter.org/json-parser",
    "created_at": "2026-03-09T19:13:22.862836Z",
    "expired_at": "2026-03-09T19:16:14.789321Z",
    "last_used_at": null,
    "click_count": 0,
    "click_limit": 25,
    "expiration_reason": "expired_at"
  }
]
```

### 9. Популярные ссылки

GET /links/popular

Возвращает список самых популярных ссылок и число переходов по ним.

Реальный пример ответа
```
[
  {
    "short_code": "comics",
    "total_clicks": 21
  },
  {
    "short_code": "back",
    "total_clicks": 5
  },
  {
    "short_code": "myproject",
    "total_clicks": 1
  }
]
```

### Дополнительный функционал, реализованный в других методах
- Создание коротких ссылок для незарегистрированных пользователей
- Ограничение на количество переходов по ссылке

## Описание таблиц

В проекте 3 таблицы:

- users
- short_links
- expired_links

### Таблица users
| Поле              | Тип                       | Ограничения          |
| ----------------- | ------------------------- | -------------------- |
| `id`              | `Integer`                 | `PRIMARY KEY`        |
| `email`           | `String(320)`             | `UNIQUE`, `NOT NULL` |
| `hashed_password` | `String(255)`             | `NOT NULL`           |
| `created_at`      | `DateTime(timezone=True)` | `NOT NULL`           |


Назначение

Хранит пользователей сервиса. Один пользователь может создать много коротких ссылок.

### Таблица short_links
| Поле                       | Тип                       | Ограничения                       |
| -------------------------- | ------------------------- | --------------------------------- |
| `id`                       | `Integer`                 | `PRIMARY KEY`                     |
| `short_code`               | `String(64)`              | `UNIQUE`, `NOT NULL`              |
| `original_url`             | `Text`                    | `NOT NULL`                        |
| `created_at`               | `DateTime(timezone=True)` | `NOT NULL`                        |
| `updated_at`               | `DateTime(timezone=True)` | `NOT NULL`                        |
| `expires_at`               | `DateTime(timezone=True)` | `NULL`                            |
| `last_used_at`             | `DateTime(timezone=True)` | `NULL`                            |
| `click_count`              | `Integer`                 | `NOT NULL`, `DEFAULT 0`           |
| `click_limit`              | `Integer`                 | `NULL`                            |
| `is_active`                | `Boolean`                 | `NOT NULL`, `DEFAULT true`        |
| `creator_user_id`          | `Integer`                 | `FOREIGN KEY -> users.id`, `NULL` |
| `created_by_authenticated` | `Boolean`                 | `NOT NULL`, `DEFAULT false`       |

Назначение

Основная таблица активных коротких ссылок.

### Таблица expired_links

| Поле                | Тип                       | Ограничения                       |
| ------------------- | ------------------------- | --------------------------------- |
| `id`                | `Integer`                 | `PRIMARY KEY`                     |
| `short_code`        | `String(64)`              | `NOT NULL`                        |
| `original_url`      | `Text`                    | `NOT NULL`                        |
| `created_at`        | `DateTime(timezone=True)` | `NOT NULL`                        |
| `expired_at`        | `DateTime(timezone=True)` | `NOT NULL`                        |
| `last_used_at`      | `DateTime(timezone=True)` | `NULL`                            |
| `click_count`       | `Integer`                 | `NOT NULL`, `DEFAULT 0`           |
| `click_limit`       | `Integer`                 | `NULL`                            |
| `creator_user_id`   | `Integer`                 | `FOREIGN KEY -> users.id`, `NULL` |
| `expiration_reason` | `String(64)`              | `NOT NULL`                        |

Назначение

Архив ссылок, которые были удалены по разным причинам.

### Запуск проекта

Приложение можно опробовать самому либо развернуть локально. В проекте есть docker-compose файл и конфигурационный файл, который откроет проект на 8000 порту при помощи команды
```
uvicorn app.main:app --reload
```
Сам же проект развёрнут при помощи сайта [render](https://three-fastapi-shortlinks.onrender.com/docs#/)
