-- ============================================================
-- Migration 034: ntfy push notifications (off until a topic is set)
-- The topic is the only secret: ntfy.sh is a public server, so use an unguessable name.
-- Each event has its own switch; all are off by default.
-- ============================================================

INSERT INTO system_config (key, value, value_type, description)
VALUES
    ('ntfy_topic', '', 'string',
     'ntfy.sh topic for push notifications. Choose an unguessable name; empty turns notifications off.'),
    ('ntfy_server', 'https://ntfy.sh', 'string',
     'ntfy server URL. Keep the default unless you run your own ntfy server.'),
    ('notify_price_alert', 'false', 'boolean',
     'Push a notification when one of your price alerts rings.'),
    ('notify_scanner_verdict', 'false', 'boolean',
     'Push a notification when the scanner verdict changes between tested edge and no tested edge.'),
    ('notify_scanner_top5', 'false', 'boolean',
     'Push a notification when a stock enters the scanner top 5.'),
    ('notify_guard_critical', 'false', 'boolean',
     'Push a notification when a risk guard alert becomes critical.')
ON CONFLICT (key) DO NOTHING;
