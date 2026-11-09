"""Validação da pulseira emitida, independente do texto do editor.

A fonte usada no editor (VisitorInfo/check-in) nem sempre representa o
credencial físico registrado em Person > Credential Management. Nunca tratar
um número pré-preenchido como prova de emissão.
"""
from __future__ import annotations

from dataclasses import dataclass

from hikcentral import HikError

PERSON_INFO = "/artemis/api/resource/v1/person/personId/personInfo"
PERSON_UPDATE = "/artemis/api/resource/v1/person/single/update"
VISITOR_INFO = "/artemis/api/visitor/v1/visitor/single/visitorinfo"


@dataclass(frozen=True)
class CardReadback:
    person_card: tuple[str, ...]
    visitor_card: tuple[str, ...] | None
    visitor_id: str
    @property
    def confirmed(self) -> bool:
        return bool(self.person_card) and (
            self.visitor_card is None or self.visitor_card == self.person_card)


def _cards(record: dict, *, required: bool = True) -> tuple[str, ...] | None:
    """None = campo não retornado; () = retornou sem credenciais."""
    if "cards" not in record:
        if not required:
            return None
        raise HikError("Consulta de credenciais Person não retornou campo cards.")
    values = record["cards"]
    if not isinstance(values, list):
        raise HikError("O HikCentral retornou credenciais em formato inválido.")
    result = []
    for item in values:
        if not isinstance(item, dict):
            raise HikError("O HikCentral retornou uma credencial inválida.")
        card = str(item.get("cardNo") or "").strip()
        if not card:
            raise HikError("Credencial Person sem cardNo; não é possível validar a pulseira.")
        result.append(card)
    if len(set(result)) != len(result):
        raise HikError("O HikCentral retornou credenciais NFC duplicadas.")
    return tuple(result)


def read_card_credential(client, visitor_id: str) -> CardReadback:
    """Duas consultas independentes, SEM alterar reserva ou check-in."""
    visitor_id = str(visitor_id or "").strip()
    if not visitor_id:
        raise HikError("Visitor ID ausente; não é possível consultar a pulseira.")
    person_response = client.post(PERSON_INFO, {"personId": visitor_id})
    person = person_response.get("data") if isinstance(person_response, dict) else None
    if not isinstance(person, dict) or str(person.get("personId") or "") != visitor_id:
        raise HikError("Person Info não confirmou a identidade do visitante.")
    visitor_response = client.post(VISITOR_INFO, {"visitorId": visitor_id})
    visitor = visitor_response.get("data") if isinstance(visitor_response, dict) else None
    if not isinstance(visitor, dict):
        raise HikError("Visitor Info não confirmou a identidade do visitante.")
    info = visitor.get("VisitorInfo") or visitor.get("visitorInfo") or visitor
    if not isinstance(info, dict) or str(info.get("visitorId") or "") != visitor_id:
        raise HikError("Visitor Info retornou identidade divergente.")
    return CardReadback(
        person_card=_cards(person, required=False) or (),
        visitor_card=_cards(info, required=False),
        visitor_id=visitor_id)


def ensure_issued_card(client, visitor_id: str, card: str, *, force_reissue: bool = True):
    """Emite/regrava no Person existente e confere ambas as APIs.

    NUNCA cria novo visitante ou reserva. A correção só é feita quando o
    Person não contém a pulseira esperada ou quando o Visitor confirma vazio.
    O Person Update é o endpoint documentado pela Hikvision para cards[].
    """
    visitor_id, card = str(visitor_id or "").strip(), str(card or "").strip()
    if not card or not visitor_id:
        raise HikError("Visitor ID e pulseira são necessários para emitir credencial.")
    current = read_card_credential(client, visitor_id)
    wanted = (card,)
    if current.person_card == wanted and (
            current.visitor_card is None or current.visitor_card == wanted):
        return current
    if len(current.person_card) > 1 or (
            current.person_card and current.person_card != wanted
            and not force_reissue):
        raise HikError("Person possui outra credencial; não substituir automaticamente.")
    # Reenviar somente a credencial, sem sobrescrever nome, grupo ou datas.
    # Repetir o mesmo valor é idempotente e pode reparar a falta de vínculo
    # entre a reserva Visitor e a credencial cadastrada em Person.
    write_error = None
    try:
        client.post(PERSON_UPDATE, {"personId": visitor_id, "cards": [{"cardNo": card}]})
    except HikError as exc:
        write_error = exc
    observed = read_card_credential(client, visitor_id)
    if observed.person_card != wanted:
        raise HikError(
            "O HikCentral não confirmou a pulseira em Credential Management "
            "(Person Info). Nenhuma nova reserva foi criada.") from write_error
    if observed.visitor_card is not None and observed.visitor_card != wanted:
        raise HikError(
            "O Person confirmou o cartão, mas Visitor Information ainda não o vinculou. "
            "Não considerar a credencial emitida; confira Credential Management.") from write_error
    return observed
