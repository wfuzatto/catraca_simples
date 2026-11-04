"""Leitura estritamente consultiva do histórico de passagens em catracas HikCentral.

Não confundir registro/check-in de visitante (registerTime), validade do
ingresso (visitStartTime/visitEndTime) e autenticação na catraca (eventTime).
O valor exibido é derivado apenas de eventos de ACESSO CONCEDIDO à pessoa
exata, com sentido de entrada/saída identificável.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from core import TZ
from hikcentral import HikError

DOOR_EVENTS_PATH = "/artemis/api/acs/v1/door/events"
PAGE_SIZE = 200
MAX_PAGES_PER_PERIOD = 12
MAX_PERIODS = 5
# Eventos de "Access Granted" documentados em OpenAPI 3.0.1, seção A.4.
# Em particular, 197151 (Access Denied by Face) NÃO conta como passagem.
GRANTED_TYPES = frozenset({
    196883, 196884, 196885, 196886, 196887, 196888, 196889,
    196890, 196891, 196892, 196893, 196894, 196895, 196896, 196897,
    197127, 198914, 198915, 200516, 110025, 110022, 110026,
})

# Algumas versões OpenAPI exigem eventType na busca. A consulta primária
# continua sendo por pessoa, sem filtro de modalidade; se ela falhar/voltar
# vazia, tentamos tipos mais comuns (face, cartão+face, multi-fator, QR).
# Não afirmar que uma consulta filtrada cobre todos os eventos possíveis.
FALLBACK_EVENT_TYPES = (196893, 196890, 196883, 198914, 110025)



def _parse_event_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
    except ValueError:
        return None


def _simplify(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(x for x in value if not unicodedata.combining(x)).upper()


READER_SEARCH_PATH = "/artemis/api/resource/v1/reader/search"

def _named_direction(value):
    """Return direction only from explicit, unambiguous reader/door labels.

    HikCentral may name a reader 'CATRACA ENTRADA 1', not just 'ENTRADA 1'.
    Whole-token matching avoids interpreting ordinary names such as 'ENTRE'.
    Ambiguous names (e.g. 'ENTRADA E SAIDA') never count as passages.
    """
    name = _simplify(value)
    entering = bool(re.search(r"(?<![A-Z0-9])(?:ENTRADA|ENT)(?![A-Z0-9])", name))
    leaving = bool(re.search(r"(?<![A-Z0-9])(?:SAIDA|SAI)(?![A-Z0-9])", name))
    if entering == leaving:
        return None
    return "entry" if entering else "exit"


def load_reader_directions(client, door_ids):
    """Read Card Reader metadata once per group, never per visitor.

    /resource/v1/reader/search returns 'indexCode' and 'name'. Map only
    readers with explicit one-way names. Missing permissions or a partial
    response must never be interpreted as an entry or an exit.
    """
    result, conflicts = {}, set()
    for door_id in dict.fromkeys(str(x) for x in door_ids if str(x).strip()):
        if len(door_id) > 64:
            continue
        response = client.post(READER_SEARCH_PATH, {"doorIndexCode": door_id})
        data = response.get("data") or {}
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise HikError(
                "HikCentral não retornou a lista de leitores por ponto de acesso.")
        for reader in rows:
            if not isinstance(reader, dict):
                continue
            rid = str(reader.get("indexCode") or reader.get("readerIndexCode") or "").strip()
            if not rid or len(rid) > 64:
                continue
            direction = _named_direction(reader.get("name") or reader.get("readerName"))
            if not direction:
                continue
            if rid in result and result[rid] != direction:
                conflicts.add(rid)
            else:
                result[rid] = direction
    for rid in conflicts:
        result.pop(rid, None)
    return result


def _direction(event, reader_directions=None):
    """Classify direction by documented attendance mode or verified names.

    Metadata reader IDs fetched from the HikCentral resource API are used
    only when the event omits a usable checkInAndOutType. Conflicting names
    are not silently forced to a direction.
    """
    mode = str(event.get("checkInAndOutType") if
               event.get("checkInAndOutType") is not None else "")
    if mode in ("1", "4", "5"):
        return "entry"
    if mode in ("2", "3", "6"):
        return "exit"
    # A reader ID is specific to the terminal, unlike a door that may
    # have both an entrance and an exit reader.
    rid = str(event.get("readerIndexCode") or "").strip()
    mapped = (reader_directions or {}).get(rid)
    labels = [
        event.get(key) for key in (
            "readerName", "doorName", "elementName", "accessPointName")
    ]
    explicit = {_named_direction(label) for label in labels}
    explicit.discard(None)
    if mapped:
        return mapped if not explicit or explicit == {mapped} else None
    return next(iter(explicit)) if len(explicit) == 1 else None


def _periods(start, end):
    """A API limita uma consulta a no máximo 31 dias."""
    if end <= start:
        return []
    windows = []
    cursor = start
    while cursor < end:
        if len(windows) >= MAX_PERIODS:
            raise HikError("Visita com período maior que o limite de consulta de passagens.")
        next_point = min(cursor + timedelta(days=30), end)
        windows.append((cursor, next_point))
        cursor = next_point
    return windows


def fetch_visitor_passages(client, record, *, now=None, reader_directions=None):
    """Consulta Visitor Access Record via ACS; classifica apenas sentidos provados.

    Primeiro filtra pelo Visitor ID. Para servidores em que esse filtro não
    encontra eventos, tenta personName, aceitando somente identidade comprovada
    por ID ou combinação exata de nome e cartão físico. Não usa nome isolado
    para atribuir passagens (homônimos podem existir).
    """
    vid = str(record.get("visitor_id") or "").strip()
    if not vid or len(vid) > 32:
        raise HikError("Visitor ID inválido para consulta de passagens.")
    start = _parse_event_time(record.get("entry_at"))
    end = _parse_event_time(record.get("exit_at"))
    if start is None or end is None or end <= start:
        raise HikError("Validade do visitante ausente ou inválida.")
    current = now or datetime.now(TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    stop = min(current.astimezone(TZ), end)
    summary = {
        "events_returned": 0, "other_visitors": 0,
        "successful_access": 0, "without_direction": 0,
        "first_entry_count": 0, "last_exit_count": 0,
        "unknown_event_type": 0, "queries": 0, "by_name_fallback": 0,
        "typed_fallback": 0, "filtered_search": 0, "unfiltered_errors": 0,
        "direction_from_reader_id": 0, "unknown_reader_codes": [],
    }
    if stop <= start:
        return {
            "first_entry_at": "", "last_exit_at": "",
            "first_access_at": "", "last_access_at": "",
            "passage_diagnostics": summary,
        }

    doors = [str(x) for x in record.get("door_ids", []) if str(x).strip()]
    if len(doors) > 10:
        doors = []  # Até 10 índices, como exige a API.
    name = str(record.get("full_name") or " ".join(
        filter(None, (record.get("first"), record.get("last"))))).strip()
    card = str(record.get("card_number") or "").strip()
    filters = [("id", {"personId": vid})]
    if name and len(name) <= 32:
        filters.append(("name", {"personName": name}))
    entry_times, exit_times, auth_times = [], [], []
    seen = set()

    def verified_person(event):
        # Nunca considerar somente o nome como identidade comprovada.
        if str(event.get("personId") or "").strip() == vid:
            return True
        return bool(card and
                    str(event.get("cardNo") or "").strip() == card and
                    _simplify(event.get("personName")) == _simplify(name))

    def process_event(event):
        summary["events_returned"] += 1
        if not isinstance(event, dict) or not verified_person(event):
            summary["other_visitors"] += 1
            return False
        try:
            event_type = int(event.get("eventType"))
        except (ValueError, TypeError):
            summary["unknown_event_type"] += 1
            return True
        if event_type not in GRANTED_TYPES:
            summary["unknown_event_type"] += 1
            return True
        when = _parse_event_time(event.get("eventTime"))
        if when is None or not start <= when <= stop:
            return True
        unique = (str(event.get("eventId") or ""), when.isoformat(),
                  str(event.get("doorIndexCode") or ""))
        if unique in seen:
            return True
        seen.add(unique)
        summary["successful_access"] += 1
        auth_times.append(when)
        direction = _direction(event, reader_directions)
        rid = str(event.get("readerIndexCode") or "").strip()
        if direction and rid and (reader_directions or {}).get(rid) == direction:
            summary["direction_from_reader_id"] += 1
        if direction == "entry":
            entry_times.append(when)
            summary["first_entry_count"] += 1
        elif direction == "exit":
            exit_times.append(when)
            summary["last_exit_count"] += 1
        else:
            summary["without_direction"] += 1
            # These are equipment IDs, never visitor/card/photo data. The
            # short list helps map still-unnamed readers in HikCentral.
            if rid and rid not in summary["unknown_reader_codes"] and (
                    len(summary["unknown_reader_codes"]) < 5):
                summary["unknown_reader_codes"].append(rid)
        return True

    def query_pages(begin, finish, identity, event_type=None):
        matched = 0
        for page in range(1, MAX_PAGES_PER_PERIOD + 1):
            payload = {
                "startTime": begin.isoformat(timespec="seconds"),
                "endTime": finish.isoformat(timespec="seconds"),
                **identity,
                "personType": 1,
                "doorIndexCodes": doors,
                "pageNo": page, "pageSize": PAGE_SIZE,
                "sortField": "SwipeTime", "orderType": 0,
            }
            if event_type is not None:
                payload["eventType"] = event_type
                summary["filtered_search"] += 1
            summary["queries"] += 1
            response = client.post(DOOR_EVENTS_PATH, payload)
            data = response.get("data") or {}
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise HikError(
                    "API Visitor Access Record retornou dados incompatíveis "
                    "(verifique endpoint e permissões ACS).")
            items = data["list"]
            try:
                total = int(data.get("total") or 0)
            except (TypeError, ValueError) as exc:
                raise HikError("Total inválido no histórico de passagens.") from exc
            for event in items:
                if process_event(event):
                    matched += 1
            if len(items) < PAGE_SIZE or (total and page * PAGE_SIZE >= total):
                return matched
        raise HikError(
            "Histórico com mais páginas que o limite seguro; horários não estimados.")

    for begin, finish in _periods(start, stop):
        for mode, identity in filters:
            matched = 0
            unfiltered_error = None
            before_events = summary["events_returned"]
            try:
                matched = query_pages(begin, finish, identity)
            except HikError as exc:
                # Nunca mascarar uma paginação incompleta usando apenas uma
                # segunda consulta: os extremos ficariam incorretos.
                if summary["events_returned"] > before_events:
                    raise
                summary["unfiltered_errors"] += 1
                unfiltered_error = exc
            if matched:
                if mode == "name":
                    summary["by_name_fallback"] += 1
                break
            # Busca por nome sem cartão/ID pode devolver homônimos. Não
            # repetir os mesmos registros em cinco modalidades de evento.
            if mode == "name" and summary["events_returned"] > before_events:
                break
            # Fallback limitado: nunca consultar todas as 22 modalidades para
            # 500 visitantes, o que sobrecarregaria a central. As direções
            # retornadas são eventos reais, mas a busca é parcial.
            summary["typed_fallback"] += 1
            typed_ok = 0
            typed_error = None
            for event_type in FALLBACK_EVENT_TYPES:
                try:
                    matched += query_pages(begin, finish, identity, event_type)
                    typed_ok += 1
                except HikError as exc:
                    typed_error = exc
                    # Quando um tipo falha, não usar extremos incompletos.
                    # Pode ser falta de permissão ACS ou incompatibilidade.
                    break
            if typed_error is not None:
                if mode == "id" and len(filters) > 1 and matched == 0:
                    continue
                raise HikError(
                    "Consulta dos tipos de evento incompleta: " + str(typed_error))
            if not typed_ok:
                if mode == "id" and len(filters) > 1:
                    continue
                raise HikError(
                    "A OpenAPI não aceitou as consultas de eventos por pessoa.") from unfiltered_error
            if mode == "name":
                summary["by_name_fallback"] += 1
            if matched:
                break

    return {
        "first_entry_at": min(entry_times).isoformat(timespec="seconds") if entry_times else "",
        "last_exit_at": max(exit_times).isoformat(timespec="seconds") if exit_times else "",
        # A tela Visitor Access Record pode mostrar autenticações sem sentido
        # físico. Não reportar último acesso como SAÍDA se não for comprovado.
        "first_access_at": min(auth_times).isoformat(timespec="seconds") if auth_times else "",
        "last_access_at": max(auth_times).isoformat(timespec="seconds") if auth_times else "",
        "passage_diagnostics": summary,
    }
