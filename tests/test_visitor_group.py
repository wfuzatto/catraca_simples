import pytest

from hikcentral import HikCentral
from remote_history import DIRECTORY_PATH, read_profile_group


@pytest.mark.parametrize(
    ("memberships", "expected"),
    [({"1"}, "Visitors"), ({"3"}, "Hóspedes Vale"), ({"1", "3"}, ""), (set(), "")],
)
def test_profile_group_matches_exact_visitor_id_only_when_membership_is_unique(
        monkeypatch, memberships, expected):
    client = HikCentral("localhost", 443, "test-key", "test-secret")
    group_path = "/artemis/api/visitor/v1/visitorgroups"

    def post(path, payload):
        if path == group_path:
            return {"data": {"VisitorGroupList": {"VisitorGroup": [
                {"indexCode": 1, "baseInfo": {"Name": "Visitors"}},
                {"indexCode": 3, "baseInfo": {"Name": "Hóspedes Vale"}},
            ]}}}
        assert path == DIRECTORY_PATH
        criteria = payload["searchCriteria"]
        assert criteria["personName"] == "Teste"
        group_id = criteria["visitorGroupID"]
        assert group_id in {"1", "3"}
        people = [{"visitorId": "9000", "visitorGivenName": "Teste",
                   "visitorFamilyName": "Homônimo"}]
        if group_id in memberships:
            people.append({"visitorId": "1000", "visitorGivenName": "Teste",
                           "visitorFamilyName": "Homônimo"})
        return {"data": {"total": len(people), "VisitorInfo": people}}

    monkeypatch.setattr(client, "post", post)
    assert client.visitor_groups() == ["Visitors", "Hóspedes Vale"]
    assert read_profile_group(client, "1000", "Teste") == expected
