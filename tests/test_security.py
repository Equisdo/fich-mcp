import json
import stat

import httpx
import pytest

from fich_mcp.remote import Moodle
from fich_mcp.security import Account, FichError, Paths, clean, download_url, private_dir


def test_consent_before_network(tmp_path):
    calls = []
    remote = Moodle(transport=httpx.MockTransport(lambda r: calls.append(r)))
    with pytest.raises(FichError, match="http_consent_required"):
        remote.login("u", "secret", accepted=False)
    assert calls == []


def test_secure_account(tmp_path):
    paths = Paths(tmp_path / "config", tmp_path / "cache")
    account = Account("secret", 7, ["core_webservice_get_site_info"], True, True)
    paths.save(account)
    assert paths.load() == account
    assert stat.S_IMODE(paths.config.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.account_file.stat().st_mode) == 0o600
    assert "password" not in paths.account_file.read_text()
    assert paths.user_cache(7) != paths.user_cache(8)
    assert "secret" not in repr(account)


def test_do_not_chmod_existing_parent(tmp_path):
    tmp_path.chmod(0o755)
    private_dir(tmp_path / "owned")
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    "url",
    [
        "https://e-fich.unl.edu.ar/moodle/webservice/pluginfile.php/a",
        "http://evil.test/moodle/webservice/pluginfile.php/a",
        "http://e-fich.unl.edu.ar:81/moodle/webservice/pluginfile.php/a",
        "http://e-fich.unl.edu.ar/moodle/webservice/pluginfile.php/../x",
    ],
)
def test_download_origin_path(url):
    with pytest.raises(FichError):
        download_url(url, "secret")


def test_recursive_redaction():
    data = {
        "token": "secret",
        "nested": [
            {
                "url": "http://x/a?token=secret&id=2",
                "html": '<a href="http://x/?wstoken=secret">hello</a>',
            }
        ],
    }
    assert "secret" not in json.dumps(clean(data, "secret"))
    assert "id=2" in json.dumps(clean(data, "secret"))


def test_login_identity_and_safe_failure():
    def handler(request):
        if request.url.path.endswith("token.php"):
            return httpx.Response(200, json={"token": "secret"})
        return httpx.Response(
            200,
            json={
                "sitename": "FICH E-Learning",
                "siteurl": "http://e-fich.unl.edu.ar/moodle",
                "userid": 7,
                "functions": [{"name": "core_webservice_get_site_info"}],
                "downloadfiles": 1,
            },
        )

    remote = Moodle(transport=httpx.MockTransport(handler))
    assert remote.login("u", "pw", accepted=True).userid == 7
    remote.close()


@pytest.mark.parametrize(
    "code,expected",
    [
        ("invalidtoken", "authentication_required"),
        ("accessexception", "unsupported_function"),
        ("requireloginerror", "course_denied"),
        ("servicenotavailable", "mobile_service_unavailable"),
    ],
)
def test_safe_remote_errors(code, expected):
    account = Account("secret", 7, ["core_course_get_contents"], True, True)
    remote = Moodle(
        account,
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "exception": "x",
                    "errorcode": code,
                    "message": "secret password arbitrary server text",
                },
            )
        ),
    )
    with pytest.raises(FichError) as error:
        remote.call("core_course_get_contents", courseid=1)
    assert error.value.code == expected
    assert "secret" not in str(error.value)


def test_write_functions_forbidden():
    remote = Moodle(Account("x", 1, ["mod_assign_submit_for_grading"], True, True))
    with pytest.raises(FichError, match="unsupported_function"):
        remote.call("mod_assign_submit_for_grading")
    remote.close()
