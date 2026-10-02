"""Parse Zerodha holdings, positions, and funds-ledger CSV exports safely."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class BrokerCSVError(ValueError):
    """The uploaded file is not a supported, internally consistent broker export."""


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lstrip("\ufeff").lower())


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BrokerCSVError("Could not read this CSV encoding. Save it as UTF-8 and retry.")


def _decimal(value: str | None, field: str, row_number: int, *, optional: bool = False) -> Decimal:
    value = (value or "").strip().replace(",", "").replace("₹", "").replace("%", "")
    if optional and not value:
        return Decimal("0")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise BrokerCSVError(f"Row {row_number}: {field} must be a number.") from exc
    if not number.is_finite():
        raise BrokerCSVError(f"Row {row_number}: {field} must be finite.")
    return number


def _parse_date(value: str | None, row_number: int) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return date.fromisoformat(date.strptime(value, fmt).isoformat())  # type: ignore[attr-defined]
        except (ValueError, AttributeError):
            pass
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise BrokerCSVError(f"Row {row_number}: posting date is not recognized.")


def _find_index(headers: list[str], *names: str) -> int | None:
    normalized = {_normalize_header(value): index for index, value in enumerate(headers)}
    for name in names:
        if _normalize_header(name) in normalized:
            return normalized[_normalize_header(name)]
    return None


def parse_broker_csv(filename: str, raw: bytes) -> dict[str, Any]:
    """Classify and validate a Zerodha export without writing to the database."""
    content = _decode(raw)
    reader = csv.reader(io.StringIO(content))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        raise BrokerCSVError("The selected CSV is empty.")

    headers = [cell.strip() for cell in rows[0]]
    norm = {_normalize_header(name) for name in headers}
    symbol_index = _find_index(headers, "instrument", "symbol", "tradingsymbol", "stock")

    ledger_headers = {"particulars", "postingdate", "debit", "credit", "netbalance"}
    if ledger_headers.issubset(norm):
        return _parse_ledger(filename, headers, rows[1:])
    if symbol_index is None:
        raise BrokerCSVError("Expected a Zerodha holdings, positions, or funds-ledger CSV.")

    return _parse_securities(
        filename,
        headers,
        rows[1:],
        kind="positions" if "product" in norm else "holdings",
        symbol_index=symbol_index,
    )


def _parse_securities(
    filename: str,
    headers: list[str],
    rows: list[list[str]],
    *,
    kind: str,
    symbol_index: int,
) -> dict[str, Any]:
    qty_index = _find_index(headers, "qty", "quantity", "shares")
    avg_index = _find_index(headers, "avg", "avg. cost", "average price", "avg price", "buy price")
    ltp_index = _find_index(headers, "ltp", "last price", "current price")
    exchange_index = _find_index(headers, "exchange", "exch", "segment")
    product_index = _find_index(headers, "product")
    if qty_index is None or avg_index is None:
        raise BrokerCSVError("A security CSV must include Instrument, Qty., and Avg. cost columns.")

    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    skipped = 0
    for row_number, row in enumerate(rows, start=2):
        if len(row) <= max(symbol_index, qty_index, avg_index):
            skipped += 1
            continue
        symbol = row[symbol_index].strip().upper()
        if not symbol:
            skipped += 1
            continue
        symbol = re.sub(r"^(NSE|BSE):", "", symbol)
        quantity_decimal = _decimal(row[qty_index], "quantity", row_number)
        if quantity_decimal != quantity_decimal.to_integral_value():
            raise BrokerCSVError(f"Row {row_number}: quantity must be a whole number.")
        quantity = int(quantity_decimal)
        average_price = _decimal(row[avg_index], "average price", row_number)
        if average_price <= 0:
            raise BrokerCSVError(f"Row {row_number}: average price must be greater than zero.")
        product = (
            row[product_index].strip().upper()
            if product_index is not None and len(row) > product_index
            else "CNC"
        ) or "CNC"
        if product not in {"CNC", "MIS", "NRML", "MTF"}:
            raise BrokerCSVError(f"Row {row_number}: unsupported Kite product {product!r}.")
        exchange = (
            row[exchange_index].strip().upper()
            if exchange_index is not None and len(row) > exchange_index
            else ""
        )
        key = (symbol, product)
        if key in seen:
            raise BrokerCSVError(
                f"Duplicate {symbol} {product} row; remove duplicates before importing."
            )
        seen.add(key)
        last_price = (
            _decimal(row[ltp_index], "last traded price", row_number, optional=True)
            if ltp_index is not None and len(row) > ltp_index
            else Decimal("0")
        )
        parsed.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "average_price": average_price,
                "last_price": last_price,
                "exchange": exchange,
                "product": product,
                "source_row": row_number,
            }
        )
    if not parsed:
        raise BrokerCSVError("No valid security rows were found in this CSV.")
    return {"kind": kind, "filename": filename, "rows": parsed, "skipped_count": skipped}


def _parse_ledger(filename: str, headers: list[str], rows: list[list[str]]) -> dict[str, Any]:
    indices = {
        "particulars": _find_index(headers, "particulars"),
        "posting_date": _find_index(headers, "posting_date", "posting date"),
        "cost_center": _find_index(headers, "cost_center", "cost center"),
        "voucher_type": _find_index(headers, "voucher_type", "voucher type"),
        "debit": _find_index(headers, "debit"),
        "credit": _find_index(headers, "credit"),
        "net_balance": _find_index(headers, "net_balance", "net balance"),
    }
    parsed: list[dict[str, Any]] = []
    skipped = 0
    for row_number, row in enumerate(rows, start=2):
        if len(row) < len(headers):
            row.extend([""] * (len(headers) - len(row)))
        values = {
            name: row[index].strip() if index is not None else "" for name, index in indices.items()
        }
        if not any(values.values()):
            skipped += 1
            continue
        posting_date = _parse_date(values["posting_date"], row_number)
        debit = _decimal(values["debit"], "debit", row_number, optional=True)
        credit = _decimal(values["credit"], "credit", row_number, optional=True)
        balance = _decimal(values["net_balance"], "net balance", row_number, optional=True)
        particulars = values["particulars"]
        voucher = values["voucher_type"]
        text = f"{voucher} {particulars}".lower()
        if posting_date is None and not voucher:
            entry_kind = "BALANCE_SNAPSHOT"
        elif "dp charge" in text:
            entry_kind = "CHARGE"
        elif "bank receipt" in text or "bank receipts" in text:
            entry_kind = "DEPOSIT"
        elif "bank payment" in text or "bank payments" in text:
            entry_kind = "WITHDRAWAL"
        elif "dividend" in text:
            entry_kind = "DIVIDEND"
        elif "interest" in text:
            entry_kind = "INTEREST"
        elif "book voucher" in voucher.lower():
            entry_kind = "TRADE_SETTLEMENT"
        else:
            entry_kind = "OTHER"
        canonical = "|".join(
            [
                posting_date.isoformat() if posting_date else "",
                particulars,
                values["cost_center"],
                voucher,
                str(debit),
                str(credit),
                str(balance),
            ]
        )
        parsed.append(
            {
                **values,
                "posting_date": posting_date,
                "debit": debit,
                "credit": credit,
                "net_balance": balance,
                "entry_kind": entry_kind,
                "import_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "source_row": row_number,
            }
        )
    if not parsed:
        raise BrokerCSVError("No ledger rows were found in this CSV.")
    return {"kind": "ledger", "filename": filename, "rows": parsed, "skipped_count": skipped}
