import sys
import types
import pytest

from devices import list_readers, read_uid, to_card_number


def install_readers(monkeypatch, factory):
    package = types.ModuleType("smartcard")
    package.__path__ = []
    system = types.ModuleType("smartcard.System")
    system.readers = factory
    monkeypatch.setitem(sys.modules, "smartcard", package)
    monkeypatch.setitem(sys.modules, "smartcard.System", system)


class MockConnection:
    def __init__(self, response=([0xA1, 0xB2, 0xC3, 0xD4], 0x90, 0x00)):
        self.response = response
        self.connected = False
        self.disconnected = False

    def connect(self):
        self.connected = True

    def transmit(self, command):
        assert command == [0xFF, 0xCA, 0x00, 0x00, 0x00]
        return self.response

    def disconnect(self):
        self.disconnected = True


class MockReader:
    def __init__(self, name, connection):
        self.name = name
        self.connection = connection

    def __str__(self):
        return self.name

    def createConnection(self):
        return self.connection


def test_pcsc_reader_detection_and_uid_read(monkeypatch):
    connection = MockConnection()
    reader = MockReader("ACS ACR122U USB Reader", connection)
    install_readers(monkeypatch, lambda: [reader])
    assert list_readers() == ["ACS ACR122U USB Reader"]
    name, uid = read_uid("ACR122U")
    assert name == "ACS ACR122U USB Reader"
    assert uid == bytes.fromhex("A1B2C3D4")
    assert connection.connected and connection.disconnected
    assert to_card_number(uid, "DECIMAL_BIG") == str(0xA1B2C3D4)


def test_pcsc_no_reader_and_stopped_service(monkeypatch):
    install_readers(monkeypatch, lambda: [])
    assert list_readers() == []
    with pytest.raises(RuntimeError, match="Nenhum leitor"):
        read_uid()
    install_readers(monkeypatch, lambda: (_ for _ in ()).throw(RuntimeError("0x8010001D")))
    with pytest.raises(RuntimeError, match="Cartão Inteligente"):
        list_readers()


def test_pcsc_rejects_invalid_uid_status(monkeypatch):
    reader = MockReader("Reader", MockConnection(([1, 2], 0x63, 0x00)))
    install_readers(monkeypatch, lambda: [reader])
    with pytest.raises(RuntimeError, match="SW=6300"):
        read_uid()
