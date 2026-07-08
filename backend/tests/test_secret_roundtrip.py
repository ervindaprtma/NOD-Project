"""§9.10 test_secret_roundtrip.py

encrypt_secret / decrypt_secret / mask_secret round-trip across all 4
notification channels (whatsapp, telegram, smtp, discord).

Lives in the security blast-radius group per docs/alert_notification_design.md
§9.10: required-to-merge, not nice-to-have.

Pure tests — no DB, no OpenSearch, no async. Run with:
    pytest tests/test_secret_roundtrip.py -v
"""
from app.core.security import encrypt_secret, decrypt_secret, mask_secret


# ── encrypt → decrypt round-trip ──────────────────────────────


def test_encrypt_decrypt_roundtrip_basic():
    """A plaintext string must come back exactly after encrypt+decrypt."""
    plain = "my-super-secret-api-token-12345"
    cipher = encrypt_secret(plain)
    assert cipher != plain, "ciphertext must differ from plaintext"
    assert decrypt_secret(cipher) == plain


def test_encrypt_decrypt_roundtrip_unicode():
    """Unicode + emoji must round-trip — real Telegram chat_ids and bot
    tokens contain non-ASCII sometimes (group display names etc.)."""
    plain = "привет-🚀-2024"
    assert decrypt_secret(encrypt_secret(plain)) == plain


def test_encrypt_decrypt_empty_string():
    """Empty string is a valid plaintext (downstream code checks truthy
    before encrypting, but encrypting '' should not crash)."""
    assert decrypt_secret(encrypt_secret("")) == ""


# ── mask_secret ───────────────────────────────────────────────


def test_mask_secret_long_value():
    """Long secret masks to first 4 + '****'."""
    out = mask_secret("my-long-api-token-12345")
    assert out == "my-l****", f"unexpected mask: {out!r}"


def test_mask_secret_short_value():
    """Values shorter than the visible_chars threshold are masked
    with a 1-char prefix + '****' (can't leak more than 1 char)."""
    out = mask_secret("abc")
    assert out == "a****", f"unexpected mask: {out!r}"


def test_mask_secret_exactly_at_threshold():
    """At exactly the visible threshold (visible_chars=4), production is
    `len(value) <= visible_chars` → takes the short branch → 'a****' (1
    char prefix), not 'abcd****'. Asserts the production behavior so
    the test catches any future refactor of mask_secret."""
    out = mask_secret("abcd")
    assert out == "a****", f"unexpected mask: {out!r}"


# ── channel-specific secret patterns (regression: all 4 channels work) ──


def test_whatsapp_api_token_roundtrip():
    """WhatsApp tokens start with 'EAA' — assert that prefix survives masking
    (so admins can identify which channel without seeing the full token)."""
    plain = "EAAKz...1234-very-long"
    cipher = encrypt_secret(plain)
    assert decrypt_secret(cipher) == plain
    masked = mask_secret(plain)
    assert masked.startswith("EAAK"), f"prefix lost: {masked!r}"


def test_telegram_bot_token_roundtrip():
    """Telegram bot tokens have form '<id>:<base64>' — long, must round-trip."""
    plain = "1234567890:AAH_B7ZqE7MqQ3vQ3vQ3vQ3vQ3vQ3vQ3vQ"
    assert decrypt_secret(encrypt_secret(plain)) == plain


def test_smtp_password_roundtrip():
    """SMTP passwords can be any string including special chars."""
    plain = "p@$$w0rd!#%&*()"
    assert decrypt_secret(encrypt_secret(plain)) == plain


def test_discord_webhook_url_roundtrip():
    """§9.2: webhook URL is a secret too — round-trip must hold."""
    plain = "https://discord.com/api/webhooks/123456789/abcdefg_token-here"
    cipher = encrypt_secret(plain)
    assert decrypt_secret(cipher) == plain
    # Masked form should still leak enough to identify the webhook
    masked = mask_secret(plain)
    assert masked.startswith("http"), f"prefix lost: {masked!r}"


# ── ciphertext must not contain the plaintext ─────────────────


def test_encrypt_never_contains_plaintext():
    """The Fernet ciphertext must not contain the plaintext substring —
    asserts the encryption is real, not a no-op."""
    plain = "obvious-secret-12345-do-not-leak"
    cipher = encrypt_secret(plain)
    assert "obvious-secret" not in cipher
    assert "do-not-leak" not in cipher
