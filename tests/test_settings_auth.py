"""A senha real não aparece em código nem em testes."""
import hashlib
import settings_auth


def test_secure_settings_verifier_with_test_only_password(monkeypatch):
    test_password = "example-test-only-password"
    test_digest = hashlib.pbkdf2_hmac(
        "sha256", test_password.encode("utf-8"),
        settings_auth._SALT, settings_auth._ITERATIONS)
    monkeypatch.setattr(settings_auth, "_EXPECTED", test_digest)
    assert settings_auth.verify_settings_password(test_password)
    assert not settings_auth.verify_settings_password(test_password + "wrong")
    assert not settings_auth.verify_settings_password("")
    assert not settings_auth.verify_settings_password(None)
