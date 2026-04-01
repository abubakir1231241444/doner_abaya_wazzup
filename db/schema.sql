-- =====================================================
-- Донер на Абае — Supabase Schema
-- Выполните этот SQL в Supabase → SQL Editor
-- =====================================================

-- ── 1. Клиенты WhatsApp ──────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    whatsapp_phone  TEXT UNIQUE NOT NULL,
    lang            TEXT DEFAULT 'ru' CHECK (lang IN ('ru', 'kz')),
    is_paused       BOOLEAN DEFAULT FALSE,  -- пауза после принятого заказа
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2. Меню ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS menu (
    id           BIGSERIAL PRIMARY KEY,
    category     TEXT NOT NULL CHECK (category IN ('Основное меню', 'Напитки', 'Ассортимент')),
    name         TEXT NOT NULL,
    price        INTEGER NOT NULL,
    is_available BOOLEAN DEFAULT TRUE,
    sort_order   INTEGER DEFAULT 0
);

-- Начальное наполнение меню
INSERT INTO menu (category, name, price, sort_order) VALUES
-- Основное меню
('Основное меню', 'Куриный донер размер-1',    1895, 1),
('Основное меню', 'Куриный донер размер-1,5',  2095, 2),
('Основное меню', 'Говяжий донер размер-1',    1995, 3),
('Основное меню', 'Говяжий донер размер-1,5',  2195, 4),
('Основное меню', 'Ассорти донер размер-1',    1995, 5),
('Основное меню', 'Ассорти донер размер-1,5',  2195, 6),
-- Напитки
('Напитки', 'Coca Cola 0.5л',   695, 1),
('Напитки', 'Sprite 0.5л',      695, 2),
('Напитки', 'Fanta 0.5л',       695, 3),
('Напитки', 'Fuse tea 0.5л',    695, 4),
('Напитки', 'Bon Aqua 0.5л',    495, 5),
('Напитки', 'Айран',            495, 6),
-- Ассортимент (добавки)
('Ассортимент', 'Красный соус', 150, 1),
('Ассортимент', 'Белый соус',   150, 2),
('Ассортимент', 'Перчик',       150, 3)
ON CONFLICT DO NOTHING;

-- ── 3. Курьеры (до orders, т.к. orders ссылается на couriers) ──
CREATE TABLE IF NOT EXISTS couriers (
    id      BIGSERIAL PRIMARY KEY,
    tg_id   BIGINT UNIQUE NOT NULL,
    name    TEXT NOT NULL,
    status  TEXT DEFAULT 'offline' CHECK (status IN ('free', 'delivering', 'offline'))
);

-- ── 4. Заказы ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT REFERENCES users(id),
    items_json       JSONB NOT NULL,           -- [{name, qty, price, size, onion, wish}]
    total_sum        INTEGER NOT NULL,
    delivery_type    TEXT NOT NULL CHECK (delivery_type IN (
                         'takeaway', 'in_cafe', 'client_courier', 'our_delivery'
                     )),
    food_wish        TEXT DEFAULT 'нет',
    delivery_address TEXT,
    delivery_phone   TEXT,
    courier_id       BIGINT REFERENCES couriers(id),
    status           TEXT DEFAULT 'new' CHECK (status IN (
                         'new','paid','preparing','waiting_courier',
                         'delivering','completed','cancelled'
                     )),
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ── 5. История диалогов ──────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id           BIGSERIAL PRIMARY KEY,
    phone        TEXT UNIQUE NOT NULL,
    messages     JSONB DEFAULT '[]'::jsonb,  -- [{role, content}]
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── Индексы ──────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders(status);
CREATE INDEX IF NOT EXISTS idx_menu_category  ON menu(category);

-- ── Аналитика (View) ─────────────────────────────────────
CREATE OR REPLACE VIEW analytics AS
SELECT
    DATE(o.created_at AT TIME ZONE 'Asia/Oral') AS date,
    COUNT(*)                                     AS total_orders,
    SUM(o.total_sum)                             AS total_revenue,
    AVG(o.total_sum)::INTEGER                    AS avg_order,
    COUNT(CASE WHEN o.delivery_type = 'our_delivery' THEN 1 END) AS delivery_count,
    COUNT(CASE WHEN o.status = 'completed' THEN 1 END)           AS completed_count
FROM orders o
GROUP BY DATE(o.created_at AT TIME ZONE 'Asia/Oral')
ORDER BY date DESC;
