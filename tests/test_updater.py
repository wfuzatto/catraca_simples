import pytest

import updater


@pytest.mark.parametrize(
    ("current", "remote", "expected"),
    [
        ("1.0.0-preview.80", "1.0.0-preview.81", True),
        ("1.0.0-preview.81", "1.0.0-preview.81", False),
        ("1.0.0-preview.81", "1.0.0-preview.80", False),
        ("1.0.0-preview.81", "1.0.0", True),
        ("1.0.0", "1.0.1-preview.1", True),
    ],
)
def test_preview_version_order(current, remote, expected):
    assert (updater._version_key(remote) > updater._version_key(current)) is expected


@pytest.mark.parametrize(
    "url",
    [
        "https://prodatastelecom.com.br/sites/catraca_vale/latest.json",
        "https://prodatastelecom.com.br/sites/catraca_vale/ValeCadastroFacialNFC.exe",
    ],
)
def test_accepts_https_assets_under_update_directory(url):
    assert updater._allowed_update_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://prodatastelecom.com.br/sites/catraca_vale/latest.json",
        "https://evil.example/sites/catraca_vale/latest.json",
        "https://prodatastelecom.com.br.evil.example/sites/catraca_vale/latest.json",
        "https://prodatastelecom.com.br/other/ValeCadastroFacialNFC.exe",
        "https://user@prodatastelecom.com.br/sites/catraca_vale/latest.json",
    ],
)
def test_rejects_untrusted_update_urls(url):
    assert not updater._allowed_update_url(url)


def test_rejects_unknown_version_format():
    with pytest.raises(ValueError):
        updater._version_key("1.0.0-preview.81+local")
