from bilibili_api import Credential

from biliparser.provider.bilibili.credential import CredentialFactory


def test_credential_serialization_uses_canonical_cookie_names():
    credential = Credential(
        sessdata="session-data",
        bili_jct="csrf-token",
        buvid3="buvid-3",
        buvid4="buvid-4",
        dedeuserid="12345",
        ac_time_value="refresh-token",
    )

    cookies = CredentialFactory._to_cookies(credential)

    assert cookies == {
        "SESSDATA": credential.sessdata,
        "bili_jct": "csrf-token",
        "buvid3": "buvid-3",
        "buvid4": "buvid-4",
        "DedeUserID": "12345",
        "ac_time_value": "refresh-token",
    }
    assert "sessdata" not in cookies
    assert "dedeuserid" not in cookies


def test_credential_restore_rejects_empty_sessdata():
    try:
        CredentialFactory._from_cookies({"SESSDATA": "", "ac_time_value": "refresh-token"})
    except ValueError as error:
        assert "SESSDATA" in str(error)
    else:
        raise AssertionError("empty SESSDATA must be rejected")
