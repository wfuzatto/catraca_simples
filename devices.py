"""Leitura opcional do UID de pulseira NFC por leitor PC/SC (ex.: ACR122U)."""
from __future__ import annotations

def to_card_number(uid: bytes, mode: str) -> str:
    if not uid:
        raise ValueError("UID vazio.")
    if mode == "HEX":
        return uid.hex().upper()
    if mode == "DECIMAL_BIG":
        return str(int.from_bytes(uid, "big"))
    if mode == "DECIMAL_LITTLE":
        return str(int.from_bytes(uid, "little"))
    raise ValueError("Formato de pulseira não suportado.")


def read_uid(reader_name: str = "") -> tuple[str, bytes]:
    try:
        from smartcard.System import readers
    except ImportError as e:
        raise RuntimeError("PC/SC indisponível: verifique pyscard e driver do leitor.") from e
    available = readers()
    if not available:
        raise RuntimeError("Nenhum leitor PC/SC encontrado. Conecte o ACR122U.")
    reader = next((r for r in available if reader_name.lower() in str(r).lower()), None) if reader_name else available[0]
    if reader is None:
        raise RuntimeError("Leitor solicitado não encontrado.")
    conn = reader.createConnection()
    conn.connect()
    try:
        data, sw1, sw2 = conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
    finally:
        conn.disconnect()
    if (sw1, sw2) != (0x90, 0x00):
        raise RuntimeError(f"Leitura UID recusada (SW={sw1:02X}{sw2:02X}).")
    return str(reader), bytes(data)
