# Restaurant Inventory & Order Management System

A backend service that lets restaurants manage their menu items and stock, and take orders **without ever selling more than they have**.

When an order comes in, the system temporarily *reserves* the stock for 15 minutes, protects against double-clicks and network retries, stays correct when many orders arrive at the same moment, and announces every new order to other systems through Kafka.

---

## Table of Contents

1. [What it does](#what-it-does)
2. [Tech stack](#tech-stack)
3. [How the pieces fit together](#how-the-pieces-fit-together)
4. [Getting started](#getting-started)
5. [Try it out](#try-it-out)
6. [API reference](#api-reference)
7. [Core concepts](#core-concepts)
8. [Database](#database)
9. [Testing](#testing)
10. [Project structure](#project-structure)
11. [Known limitations](#known-limitations)
12. [Future work](#future-work)
    - [Backend and production hardening](#backend-and-production-hardening)
    - [Where AI can be used](#where-ai-can-be-used)
    - [Production-level frontend](#production-level-frontend)

---

## What it does

- Manage **restaurants**, their **products** (menu items with prices) and the **inventory** for each product.
- **Place orders** with one or more items. Stock is moved from *available* to *reserved* in one atomic step.
- **Retry-safe ordering**: sending the same request twice (same idempotency key) never creates two orders.
- **Race-safe ordering**: if two customers try to buy the last items at the same time, exactly one wins and the other gets a clear "insufficient inventory" error.
- **Reservation expiry**: unpaid or unconfirmed orders release their stock automatically after 15 minutes (once the expiry job is triggered, see [Known limitations](#known-limitations)).
- **Event publishing**: every new order publishes an `ORDER_CREATED` event to Kafka so other services (kitchen display, analytics, notifications) can react.
- **Fast reads**: product and restaurant lists are cached in Redis.

---

## Tech stack

| Layer | Technology | Why it is here |
|---|---|---|
| Language | Python 3.12+ (developed on 3.14) | Readable, great ecosystem |
| Web framework | **FastAPI** + **Uvicorn** | Fast, async-ready, automatic interactive API docs |
| Validation | **Pydantic v2** / pydantic-settings | Request validation and typed configuration |
| ORM | **SQLAlchemy 2.x** | Typed models, transactions, row-level locking |
| Database | **PostgreSQL 16** (via `psycopg` 3) | Reliable ACID transactions, `SELECT ... FOR UPDATE` |
| Migrations | **Alembic** | Versioned, repeatable schema changes |
| Cache | **Redis 7** | Caches read-heavy lists, with a 60 s TTL |
| Messaging | **Apache Kafka 3.9** (KRaft mode, no ZooKeeper) | Publishes order events to other systems |
| Local infra | **Docker Compose** | One command starts Postgres, Redis and Kafka |

---

## How the pieces fit together

```mermaid
flowchart LR
    Client["Client<br/>(curl, Swagger UI, future frontend)"] --> API["FastAPI app"]
    API -->|"read / write<br/>(transactions, row locks)"| PG[("PostgreSQL")]
    API -->|"cache-aside<br/>(60 s TTL)"| R[("Redis")]
    API -->|"ORDER_CREATED event"| K[["Kafka<br/>topic: orders.events"]]
    K -.-> Future["Future consumers:<br/>kitchen display, analytics,<br/>notifications, AI services"]
```

The lifecycle of a single order:

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant DB as PostgreSQL
    participant K as Kafka

    C->>API: POST /orders (items + idempotency_key)
    API->>DB: Does this key already exist?
    alt Same key, same request
        DB-->>API: existing order
        API-->>C: return the original order (no duplicate)
    else Same key, different request
        API-->>C: 409 Conflict
    else New key
        API->>DB: INSERT order (claims the unique key)
        loop each product, lowest ID first
            API->>DB: SELECT inventory ... FOR UPDATE (row lock)
            API->>DB: available -= qty, reserved += qty
            API->>DB: INSERT order item + 15 min reservation
        end
        API->>DB: COMMIT (all or nothing)
        API->>K: publish ORDER_CREATED (best effort)
        API-->>C: order + reservations
    end
```

---

## Getting started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose
- Python 3.12 or newer
- Git

### 1. Start the infrastructure

From the project root:

```bash
docker compose up -d
```

This starts three containers:

| Service | Container | Port on your machine |
|---|---|---|
| PostgreSQL | `restaurant-postgres` | `5432` |
| Redis | `restaurant-redis` | `6379` |
| Kafka | `restaurant-kafka` | `29092` |

Check that they are healthy:

```bash
docker compose ps
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install kafka-python requests
```

> **Why the second `pip install`?**
> The code imports `kafka` (in `app/events.py`) and `requests` (in `test_concurrency.py`), but neither package is listed in `requirements.txt` yet. Add `kafka-python` and `requests` to that file so a fresh install works with one command.

### 3. Configure the environment

The app reads its database connection from a `.env` file in the project root:

```env
DATABASE_URL=postgresql+psycopg://restaurant_user:restaurant_password@localhost:5432/restaurant_db
```

These values match the credentials in `docker-compose.yml`. Two optional settings have sensible defaults and only need setting if you change the ports:

| Variable | Default |
|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:29092` |

### 4. Create the database tables

```bash
alembic upgrade head
```

### 5. Run the API

```bash
uvicorn app.main:app --reload
```

The API is now live:

- Health check: <http://127.0.0.1:8000/>
- **Interactive docs (Swagger UI): <http://127.0.0.1:8000/docs>**
- Alternative docs (ReDoc): <http://127.0.0.1:8000/redoc>

### Stopping and resetting

```bash
docker compose down        # stop containers, keep data
docker compose down -v     # stop containers AND delete all data (fresh start)
```

---

## Try it out

Create some starter data, then place an order. (You can also do all of this by clicking through <http://127.0.0.1:8000/docs>.)

```bash
# 1. Create a restaurant
curl -X POST "http://127.0.0.1:8000/restaurants/?name=Green%20Fork&address=12%20Main%20St"

# 2. Add a product to it (restaurant_id=1)
curl -X POST "http://127.0.0.1:8000/products/?restaurant_id=1&name=Burger&price=9.99"

# 3. Stock 5 of that product (product_id=1)
curl -X POST "http://127.0.0.1:8000/inventory/?product_id=1&available_quantity=5"

# 4. Place an order for 2 burgers
curl -X POST "http://127.0.0.1:8000/orders/" \
  -H "Content-Type: application/json" \
  -d '{
        "restaurant_id": 1,
        "items": [{ "product_id": 1, "quantity": 2 }],
        "idempotency_key": "my-first-order"
      }'
```

Example response:

```json
{
  "order_id": 1,
  "restaurant_id": 1,
  "status": "PENDING",
  "total_amount": "19.98",
  "items": [
    { "product_id": 1, "quantity": 2, "unit_price": "9.99" }
  ],
  "reservations": [
    {
      "inventory_id": 1,
      "quantity": 2,
      "status": "ACTIVE",
      "expires_at": "2026-10-11T14:15:00.000000"
    }
  ]
}
```

Now **run the same order command again**. You get the same `order_id` back, and the stock is not reserved a second time. That is idempotency at work.

### See the Kafka event

```bash
docker exec -it restaurant-kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic orders.events --from-beginning
```

You should see a message like:

```json
{"event_type": "ORDER_CREATED", "order_id": 1, "restaurant_id": 1, "status": "PENDING", "total_amount": "19.98", "created_at": "..."}
```

### Release expired reservations

After 15 minutes, call:

```bash
curl -X POST "http://127.0.0.1:8000/orders/expire-reservations"
```

---

## API reference

| Method | Path | Purpose | Input |
|---|---|---|---|
| `GET` | `/` | Health check | none |
| `POST` | `/restaurants/` | Create a restaurant | query: `name`, `address` |
| `GET` | `/restaurants/` | List restaurants (cached 60 s) | none |
| `POST` | `/products/` | Create a product | query: `restaurant_id`, `name`, `price`, optional `description` |
| `GET` | `/products/` | List products (cached 60 s) | none |
| `POST` | `/inventory/` | Create the stock record for a product | query: `product_id`, `available_quantity` |
| `POST` | `/orders/` | Place an order (idempotent) | JSON body, see below |
| `POST` | `/orders/expire-reservations` | Release stock from expired reservations | none |

**Order request body**

```json
{
  "restaurant_id": 1,
  "items": [{ "product_id": 1, "quantity": 2 }],
  "idempotency_key": "any-unique-string-up-to-100-chars"
}
```

**Order error codes**

| Status | Meaning |
|---|---|
| `400` | The same product appears twice in one order |
| `404` | Product not found in that restaurant, or the product has no inventory record |
| `409` | Not enough stock, **or** the idempotency key was reused with a different request |
| `422` | Invalid body (for example quantity ≤ 0, empty items list) |

---

## Core concepts

### 1. Idempotency (safe retries)

Networks fail and users double-click. Every order carries an **idempotency key** chosen by the client.

- The server stores the key on the order (it has a `UNIQUE` constraint) together with a **SHA-256 hash of the request** (restaurant + items, sorted so item order does not matter).
- **Same key + same request** returns the original order. Nothing new is created.
- **Same key + different request** returns `409 Conflict`, because that is almost certainly a client bug.
- If two identical requests arrive at the *exact same instant*, the database's unique constraint lets only one insert succeed. The loser catches the error, rolls back and returns the winner's order.

### 2. Concurrency control (no overselling)

When ordering, each inventory row is read with `SELECT ... FOR UPDATE`. This is a **row-level lock**: a second order for the same product waits until the first one commits, then sees the updated stock.

To prevent **deadlocks** (order A waits for B while B waits for A), locks are always taken in **ascending product ID order**, no matter how the items were listed in the request.

### 3. Stock reservations

Stock is tracked in two numbers per product:

- `available_quantity`: what can still be sold
- `reserved_quantity`: what is held by pending orders

Placing an order moves stock from available to reserved and creates a `Reservation` that expires in **15 minutes**. Order, items, reservations and inventory changes are saved in **one transaction**: either everything happens or nothing does.

### 4. Reservation expiry

`POST /orders/expire-reservations` finds active reservations past their expiry time, returns their quantity to `available_quantity` and marks them `EXPIRED`. If an order has no active reservations left, the order itself becomes `EXPIRED`.

It uses `FOR UPDATE SKIP LOCKED`, so several workers can run it simultaneously without fighting over the same rows, and it locks inventory in a consistent order.

### 5. Caching (Redis, cache-aside)

`GET /products/` and `GET /restaurants/` first look in Redis; on a miss they query PostgreSQL and store the result for 60 seconds. Creating a product or restaurant **deletes the cached list** right after the commit.

The cache **fails open**: if Redis is down, the app quietly falls back to the database instead of returning errors.

### 6. Event streaming (Kafka)

After a new order is committed, an `ORDER_CREATED` event is published to the `orders.events` topic, keyed by order ID. This decouples the order service from everything that wants to react to orders.

Publishing is **best effort**: if Kafka is unavailable the order is still saved and the failure is logged. (See [Future work](#backend-and-production-hardening) for the outbox pattern, which makes this fully reliable.)

---

## Database

### Entity relationships

```mermaid
erDiagram
    RESTAURANTS ||--o{ PRODUCTS : offers
    RESTAURANTS ||--o{ ORDERS : receives
    PRODUCTS ||--o| INVENTORY : "stocked in"
    ORDERS ||--|{ ORDER_ITEMS : contains
    PRODUCTS ||--o{ ORDER_ITEMS : "ordered as"
    ORDERS ||--o{ RESERVATIONS : holds
    INVENTORY ||--o{ RESERVATIONS : "reserved from"

    RESTAURANTS {
        int id PK
        string name
        string address
        datetime created_at
    }
    PRODUCTS {
        int id PK
        int restaurant_id FK
        string name
        string description
        numeric price
    }
    INVENTORY {
        int id PK
        int product_id FK "unique"
        int available_quantity
        int reserved_quantity
    }
    ORDERS {
        int id PK
        int restaurant_id FK
        string status "PENDING or EXPIRED"
        numeric total_amount
        string idempotency_key "unique"
        string request_hash
        datetime created_at
    }
    ORDER_ITEMS {
        int id PK
        int order_id FK
        int product_id FK
        int quantity
        numeric unit_price
    }
    RESERVATIONS {
        int id PK
        int order_id FK
        int inventory_id FK
        int quantity
        string status "ACTIVE or EXPIRED"
        datetime expires_at
        datetime created_at
    }
```

### Tables at a glance

| Table | Purpose | Worth knowing |
|---|---|---|
| `restaurants` | Each restaurant | Root of the data model |
| `products` | Menu items, per restaurant | `price` is `NUMERIC(10,2)`, never a float |
| `inventory` | Stock level per product | One row per product (`UNIQUE product_id`); this is the row that gets locked |
| `orders` | Order header | `idempotency_key` is unique; `request_hash` detects mismatched retries |
| `order_items` | Line items | `unit_price` is a **snapshot** of the price at order time, so later price changes do not rewrite history |
| `reservations` | Stock holds | One per ordered product; has an `expires_at` |

### Migrations

Schema changes are versioned in `alembic/versions/` and applied in a straight line:

```
restaurants → products → inventory → orders tables → reservations
            → product price → order idempotency key → request hash
```

Handy commands:

```bash
alembic upgrade head                              # apply all migrations
alembic current                                   # which version is the DB on?
alembic history                                   # list all migrations
alembic revision --autogenerate -m "add thing"    # generate a migration after editing models
alembic downgrade -1                              # undo the last migration
```

> Two migration files are both named `add_order_request_hash`. The first is an empty placeholder and the second actually adds the column. Both are harmless, but you can squash them later.

---

## Testing

Two helper scripts live in the project root. They are manual checks rather than an automated test suite.

**`test_db.py`: can the app reach the database?**

```bash
python test_db.py
# Database connection successful!
```

**`test_concurrency.py`: does the system really prevent overselling?**

It fires two orders for 4 units of product 1 at the same moment, using different idempotency keys.

1. Make sure product 1 has exactly **5** units in stock (a fresh `POST /inventory/` with `available_quantity=5` does this).
2. Start the API, then run:

```bash
python test_concurrency.py
```

**Expected result:** one request succeeds (`200`) and the other fails with `409 Insufficient inventory`. Never two successes. Seeing both succeed would mean the locking is broken.

> The idempotency keys are fixed (`concurrency-test-A` / `-B`), so a second run returns the *saved* results instead of testing again. Change the keys, or reset with `docker compose down -v`, to repeat the experiment.

---

## Project structure

```
restaurant-inventory-system/
├── app/
│   ├── main.py              # FastAPI app; registers all routers
│   ├── cache.py             # Redis helpers (get / set / delete, fail-open)
│   ├── events.py            # Kafka producer + publish_order_event()
│   ├── db/
│   │   ├── base.py          # SQLAlchemy declarative Base
│   │   ├── database.py      # Settings (.env), engine, SessionLocal
│   │   └── dependencies.py  # get_db() session dependency
│   ├── models/              # Database tables
│   │   ├── restaurant.py  product.py  inventory.py
│   │   └── order.py  order_item.py  reservation.py
│   ├── schemas/
│   │   └── order.py         # Pydantic request validation for orders
│   └── routes/
│       ├── restaurant.py  product.py  inventory.py
│       └── order.py         # The heart of the system
├── alembic/                 # Migration environment + versions/
├── alembic.ini
├── docker-compose.yml       # Postgres, Redis, Kafka
├── requirements.txt
├── test_db.py               # DB connectivity check
├── test_concurrency.py      # Oversell / race-condition check
└── .env                     # DATABASE_URL (do not commit)
```

---

## Known limitations

Being honest about where the project stands today:

- **Expiry is manual.** Nothing calls `/orders/expire-reservations` automatically yet, so expired holds are only released when someone triggers it.
- **Orders have no lifecycle past `PENDING`.** There is no confirm, pay, cancel or fulfil step, so reserved stock is never permanently deducted.
- **No authentication or authorization.** Anyone who can reach the API can do anything.
- **Limited read endpoints.** There is no way to fetch an order by ID, list orders, or view inventory yet.
- **Create endpoints take query parameters** rather than JSON bodies. This works, but is unusual for `POST`.
- **No CORS configuration**, so a browser-based frontend on another origin will be blocked until it is added.
- **Kafka publishing is not transactional.** If the app crashes between the database commit and the publish, the event is lost.
- **Shared product cache key.** `products:all` caches every restaurant's products together.
- **Development settings**: SQL logging is on (`echo=True`), and credentials are hard-coded for local use.
- **Heads-up when sharing the project:** don't zip or commit `.venv/` and `.env`. Add a `.gitignore` containing at least `.venv/`, `.env`, `__pycache__/`.

---

## Future work

### Backend and production hardening

**Correctness and features**
- [ ] **Order lifecycle**: `PENDING → CONFIRMED → FULFILLED / CANCELLED`, converting reservations into permanent stock deductions.
- [ ] **Automatic expiry** with a scheduler or worker (APScheduler, Celery beat, or a Kafka-driven job) instead of a manually-called endpoint.
- [ ] **Transactional outbox** for Kafka: write the event into an `outbox` table in the same transaction as the order, and let a relay publish it, so events are never lost.
- [ ] **Stock movement ledger**: an append-only table recording every change (restock, sale, expiry, waste, correction) for auditing.
- [ ] Endpoints to **restock/adjust inventory**, **get/list orders**, update and delete products, with **pagination and filtering**.
- [ ] Database `CHECK` constraints (`available_quantity >= 0`) as a safety net, plus indexes on `reservations(status, expires_at)` and `order_items(order_id)`.

**Security**
- [ ] Authentication (OAuth2/OIDC with JWT) and **role-based access** (owner, manager, staff).
- [ ] **Multi-tenancy**: derive `restaurant_id` from the logged-in user, never trust it from the request.
- [ ] Rate limiting, CORS allow-list, secrets from a secret manager rather than `.env`.

**Operations**
- [ ] A `Dockerfile` for the API and adding it to `docker-compose.yml`; CI pipeline (lint, tests, build).
- [ ] A real automated test suite with `pytest` (unit tests, API tests, and the concurrency test as a proper test).
- [ ] Structured logging, metrics and tracing with OpenTelemetry, plus `/health` and `/ready` endpoints.
- [ ] Connection pool tuning, timezone-aware timestamps (replace `datetime.utcnow`), Kafka/Redis settings moved into the `Settings` class.
- [ ] Production-grade Kafka (multiple brokers, replication, partitions) and managed Postgres/Redis.

### Where AI can be used

None of this exists yet, but the architecture is a good fit. The easiest entry point is a **Kafka consumer** reading `orders.events`, so AI features run as separate services and never slow down order taking.

| Idea | What it does | Data it would use |
|---|---|---|
| **Demand forecasting** | Predicts how much of each item will sell per day or week (weekends, holidays, weather, local events) | `orders` + `order_items` history |
| **Smart reordering** | Suggests what to buy and when, per product, based on forecasts and supplier lead times | Forecasts + `inventory` levels |
| **Waste and spoilage reduction** | Flags items likely to expire unsold and suggests specials or discounts | Stock levels + sales velocity + expiry dates |
| **Anomaly detection** | Spots unusual stock drops, suspicious order patterns or possible theft/fraud | Stock movement ledger + order events |
| **Dynamic reservation time** | Learns how long customers really take to confirm and tunes the 15-minute hold per situation | `reservations` outcomes |
| **Natural-language assistant** | Staff ask "How many burgers can we still sell today?" or "What should I order for Saturday?" and an LLM answers by calling the API as tools | The API + forecasts |
| **Invoice / delivery OCR** | Photograph a supplier invoice and automatically add the stock | Image input → inventory restock endpoint |
| **Menu and pricing insights** | Identifies best-sellers, low-margin items and good bundle or upsell candidates | Sales history + product costs |

Practical notes: start with a simple statistical baseline (moving averages, Prophet) before reaching for deep learning; keep a human approving purchase orders; and log every AI suggestion with its outcome so you can measure whether it actually helps.

### Production-level frontend

There is no frontend yet. Here is what a production-quality one would look like.

**Recommended stack**

| Concern | Suggestion |
|---|---|
| Framework | **Next.js (React) with TypeScript** |
| Styling / components | Tailwind CSS + **shadcn/ui** (accessible, customizable) |
| Server data | **TanStack Query** for caching, refetching and optimistic updates |
| Forms and validation | React Hook Form + **Zod** |
| API client | Generate a **typed client from FastAPI's OpenAPI schema** (`openapi-typescript` or `orval`), so frontend and backend can't drift apart |
| Auth | OIDC provider (Auth0, Clerk, Keycloak) with role-aware routes |
| Real-time | WebSockets or Server-Sent Events, fed from Kafka events, for live order and stock updates |
| Charts | Recharts or Tremor |
| Testing | Vitest + React Testing Library (units), **Playwright** (end-to-end) |
| Monitoring | Sentry for errors, web-vitals for performance |

**Screens to build**

1. **Dashboard**: today's orders, revenue, low-stock alerts, expiring reservations.
2. **Inventory**: searchable table with stock levels, inline restock/adjust, low-stock highlighting.
3. **Menu / products**: create and edit items and prices per restaurant.
4. **New order**: pick items, live availability check, submit (with an auto-generated idempotency key that is **reused if the user retries**).
5. **Orders**: live list with status, and a countdown to each reservation's expiry.
6. **Reports**: sales trends, best-sellers, waste (and later AI forecasts).
7. **Kitchen display**: a big-text, tablet-friendly view of incoming orders.

**What "production level" means here**
- Responsive, **accessible** (keyboard navigation, screen-reader labels, WCAG contrast) and installable as a **PWA** for tablets.
- Friendly loading skeletons, empty states and error boundaries; clear messages for `409` (out of stock) and network failures.
- Role-based UI (staff vs manager), session expiry handling, no secrets in the browser.
- Environment-based config, CI with lint, type-check and tests, preview deployments, and a CDN (Vercel, Cloudflare or similar).
- Internationalization if you plan to serve more than one language or currency.

**Backend changes the frontend will need first:** CORS configuration, authentication, list/get endpoints for orders and inventory, pagination, and JSON bodies for create endpoints.

---

## Contributing

1. Create a branch.
2. Make your change; if you edit a model, generate and review an Alembic migration.
3. Run `test_db.py` and `test_concurrency.py` (or your `pytest` suite, once added).
4. Open a pull request describing what changed and why.
