"""Edição preserva nível homologado do grupo original, sem preferência do caixa."""
import pytest

from core import default_settings
from visitor_editor import selected_editor_access


def test_current_group_uses_configured_visitor_level():
    settings = default_settings()
    groups = ["Day use", "Hóspedes Serra", "Hóspedes Vale"]
    assert selected_editor_access("Hóspedes Vale", groups, settings) == ["VISITANTE_VALE"]


def test_mapping_for_original_group_returns_corresponding_visitor_level():
    settings = default_settings()
    groups = ["Day use", "Hóspedes Serra", "Hóspedes Vale"]
    assert selected_editor_access("Hóspedes Serra", groups, settings) == ["VISITANTE_SERRA"]
    assert selected_editor_access("Day use", groups, settings) == ["DAY_USE"]


def test_disallows_unknown_groups_and_unmapped_grants():
    settings = default_settings()
    groups = ["Hóspedes Vale", "Visitors"]
    with pytest.raises(ValueError, match="não confirmado"):
        selected_editor_access("Hóspedes Serra", groups, settings)
    with pytest.raises(ValueError, match="Configure exatamente um nível Visitor"):
        selected_editor_access("Visitors", groups, settings)


def test_disallows_nonapproved_access_level_even_if_saved_in_config():
    settings = default_settings()
    settings["group_access"]["Hóspedes Vale"] = ["NIVEL_ADMINISTRADOR"]
    with pytest.raises(ValueError, match="homologado"):
        selected_editor_access("Hóspedes Vale", ["Hóspedes Vale"], settings)


def test_preferred_group_does_not_override_existing_visitor_group():
    # A preferência bloqueia somente NOVOS cadastros neste caixa.
    # Um visitante existente de Serra deve manter Serra sem cair em Vale.
    settings = default_settings()
    settings["preferred_group"] = "Hóspedes Vale"
    assert selected_editor_access(
        "Hóspedes Serra", ["Hóspedes Vale", "Hóspedes Serra"], settings
    ) == ["VISITANTE_SERRA"]
    assert selected_editor_access(
        "Hóspedes Vale", ["Hóspedes Vale"], settings
    ) == ["VISITANTE_VALE"]


def test_editor_builds_payload_with_selected_group_without_affecting_valid_times():
    from core import Visitor, registration_payload, TZ
    from datetime import datetime, timedelta
    settings = default_settings()
    chosen = "Hóspedes Serra"
    assert selected_editor_access(chosen, ["Hóspedes Vale", chosen], settings) == ["VISITANTE_SERRA"]
    start = datetime(2099, 11, 3, 9, tzinfo=TZ)
    visitor = Visitor("Teste", "Visitante", chosen, b"", "123123123",
                      start, start + timedelta(hours=8), "")
    payload = registration_payload(visitor, 0)
    assert payload["visitorInfoList"][0]["VisitorInfo"]["visitorGroupName"] == chosen
    assert payload["visitorInfoList"][0]["VisitorInfo"]["accessInfo"]["qrCodeValidNum"] == 0
