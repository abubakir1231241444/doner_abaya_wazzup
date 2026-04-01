"""
Тесты для pdf_validator.py — без реального PDF, с mock-данными.
"""
import pytest
from unittest.mock import patch
from datetime import datetime, timedelta
import pytz

from src.pdf_validator import validate_receipt, _parse_amount, _parse_datetime, _check_merchant
from src.config import KASPI_MERCHANT_NAME, KASPI_MERCHANT_BIN

TZ = pytz.timezone("Asia/Oral")


def make_fake_text(amount: int, minutes_ago: int = 5, merchant: str = "ИП ДОНЕР А") -> str:
    """Генерирует текст, похожий на Kaspi-чек."""
    dt = datetime.now(TZ) - timedelta(minutes=minutes_ago)
    dt_str = dt.strftime("%d.%m.%Y %H:%M")
    return (
        f"Покупки\n"
        f"{merchant}\n"
        f"Платеж успешно совершен\n"
        f"{amount} ₸\n"
        f"Дата и время по Астане {dt_str}\n"
        f"ИИН/БИН продавца {KASPI_MERCHANT_BIN}\n"
    )


class TestParseAmount:
    def test_tenge_sign(self):
        assert _parse_amount("2590 ₸") == 2590

    def test_spaced_amount(self):
        assert _parse_amount("2 590 ₸") == 2590

    def test_keyword_summa(self):
        assert _parse_amount("Итого: 1895 тг") == 1895

    def test_no_amount(self):
        assert _parse_amount("просто текст без суммы") is None


class TestParseDatetime:
    def test_valid_date(self):
        dt = _parse_datetime("Дата и время 18.03.2026 16:05")
        assert dt is not None
        assert dt.hour == 16
        assert dt.minute == 5

    def test_no_date(self):
        assert _parse_datetime("нет даты тут") is None


class TestMerchantCheck:
    def test_name_match(self):
        assert _check_merchant("ИП ДОНЕР А — кафе") is True

    def test_bin_match(self):
        assert _check_merchant(f"ИИН/БИН: {KASPI_MERCHANT_BIN}") is True

    def test_no_match(self):
        assert _check_merchant("Другой магазин 999999999") is False


class TestValidateReceipt:
    def _make_bytes(self, text: str) -> bytes:
        """Возвращает mock PDF bytes, с патчем на _extract_text."""
        return b"%PDF-mock"

    def _patch_extract(self, text: str):
        return patch("src.pdf_validator._extract_text", return_value=text)

    def test_valid_receipt(self):
        text = make_fake_text(1895, minutes_ago=3)
        with self._patch_extract(text):
            r = validate_receipt(b"%PDF-mock", 1895)
        assert r.ok is True

    def test_wrong_amount(self):
        text = make_fake_text(1000, minutes_ago=3)
        with self._patch_extract(text):
            r = validate_receipt(b"%PDF-mock", 1895)
        assert r.ok is False
        assert "не совпадает" in r.error

    def test_old_receipt(self):
        text = make_fake_text(1895, minutes_ago=20)
        with self._patch_extract(text):
            r = validate_receipt(b"%PDF-mock", 1895)
        assert r.ok is False
        assert "старый" in r.error

    def test_empty_pdf(self):
        r = validate_receipt(b"", 1895)
        assert r.ok is False

    def test_unreadable_pdf(self):
        with self._patch_extract(""):
            r = validate_receipt(b"%PDF-mock", 1895)
        assert r.ok is False
