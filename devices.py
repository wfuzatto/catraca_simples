"""Leitura opcional do UID de pulseira NFC por leitor PC/SC (ex.: ACR122U)."""
from __future__ import annotations

def _reader_objects():
    try:
        from smartcard.System import readers
        return list(readers())
    except ImportError as exc:
        raise RuntimeError("PC/SC indisponível: instale pyscard e o driver do leitor.") from exc
    except Exception as exc:
        if "0x8010001D" in str(exc) or "resource manager" in str(exc).lower():
            raise RuntimeError("O serviço Windows Cartão Inteligente (PC/SC) não está em execução.") from exc
        raise RuntimeError(f"Não foi possível consultar leitores PC/SC: {type(exc).__name__}.") from exc


def list_readers() -> list[str]:
    """Return connected PC/SC reader names without reading any card."""
    return [str(reader) for reader in _reader_objects()]


def to_card_number(uid: bytes, mode: str) -> str:
    """Conversões explícitas: inverter bytes NÃO é o mesmo que inverter bits."""
    if not uid:
        raise ValueError("UID vazio.")
    if mode == "HEX":
        return uid.hex().upper()
    if mode == "DECIMAL_BIG":
        return str(int.from_bytes(uid, "big"))
    if mode == "DECIMAL_LITTLE":
        return str(int.from_bytes(uid, "little"))
    if mode == "DECIMAL_BITS_REVERSED":
        # Reversão de toda a sequência de bits do UID, mantendo o comprimento.
        inverted = int(f"{int.from_bytes(uid, 'big'):0{8*len(uid)}b}"[::-1], 2)
        return str(inverted)
    if mode == "DECIMAL_EACH_BYTE_BITS_REVERSED":
        inverted = bytes(int(f"{byte:08b}"[::-1], 2) for byte in uid)
        return str(int.from_bytes(inverted, "big"))
    raise ValueError("Formato de pulseira não suportado.")


def read_uid(reader_name: str = "") -> tuple[str, bytes]:
    available = _reader_objects()
    if not available:
        raise RuntimeError("Nenhum leitor PC/SC encontrado. Conecte o ACR122U.")
    reader = next((r for r in available if reader_name.lower() in str(r).lower()), None) if reader_name else available[0]
    if reader is None:
        raise RuntimeError("Leitor solicitado não encontrado.")
    conn = reader.createConnection()
    try:
        conn.connect()
    except Exception as exc:
        raise RuntimeError("Não foi possível ler a pulseira. Confira o leitor e aproxime um cartão.") from exc
    try:
        data, sw1, sw2 = conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
    finally:
        conn.disconnect()
    if (sw1, sw2) != (0x90, 0x00):
        raise RuntimeError(f"Leitura UID recusada (SW={sw1:02X}{sw2:02X}).")
    return str(reader), bytes(data)
