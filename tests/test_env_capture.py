"""compare_env: the summary fills canvas/webgl/audio, and bundles go to a file (#22)."""

import hashlib
import json
import shutil
import subprocess

import pytest

from camoufox_reverse_mcp.tools import jsvmp


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_capture_script_is_a_function_expression():
    script = jsvmp._env_capture_script()
    check = "const f = eval(require('fs').readFileSync(0, 'utf8')); process.stdout.write(typeof f);"
    out = subprocess.run([shutil.which("node"), "-e", check], input="(" + script + ")",
                         capture_output=True, text=True, encoding="utf-8", check=True)
    assert out.stdout == "function"


class _Page:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def evaluate(self, script, arg=None):
        self.calls.append(arg)
        return self.data


def _manager(page):
    class Manager:
        async def get_active_page(self):
            return page
    return Manager()


async def test_summary_is_returned_inline(monkeypatch):
    page = _Page({"navigator": {}, "canvas": {"data_url_length": 10, "data_url": "data:image/png;base64,AA"}})
    monkeypatch.setattr(jsvmp, "browser_manager", _manager(page))
    result = await jsvmp.compare_env(properties=["navigator.oscpu"])
    assert result["canvas"]["data_url_length"] == 10 and "data_url" not in result["canvas"]
    assert result["canvas"]["data_url_sha256"] == hashlib.sha256(b"data:image/png;base64,AA").hexdigest()
    assert page.calls == [{"full": False, "sections": [], "custom": ["navigator.oscpu"]}]


async def test_bundle_is_written_to_the_output_path(monkeypatch, tmp_path):
    data = {
        "navigator": {"userAgent": {"type": "string", "value": "UA"}, "platform": {"type": "string", "value": "P"}},
        "window": {"location": {"origin": "https://example.test", "pathname": "/"}},
        "css": {"props": ["color", "display"], "for_in_count": 1542},
        "canvas": {"data_url_length": 43806, "data_url": "data:image/png;base64,AAAA"},
        "webgl": {"renderer": "R", "extension_count": 29},
        "iframe": {"differs_from_top": []},
    }
    page = _Page(data)
    monkeypatch.setattr(jsvmp, "browser_manager", _manager(page))
    out = tmp_path / "env" / "bundle.json"
    result = await jsvmp.compare_env(output_path=str(out), sections=["navigator", "css", "canvas"])
    assert page.calls[0]["full"] is True and page.calls[0]["sections"] == ["navigator", "css", "canvas"]
    bundle = json.loads(out.read_text(encoding="utf-8"))
    assert bundle["schema"] == "camoufox-reverse-mcp/env-bundle@1" and bundle["world"] == "isolated"
    assert bundle["canvas"]["data_url"].startswith("data:image/png")
    assert result["output_path"] == str(out.resolve()) and result["bytes"] > 0
    assert result["summary"] == {"userAgent": "UA", "navigator_getters": 2, "css_props": 2,
                                 "css_for_in_count": 1542, "canvas_data_url_length": 43806,
                                 "webgl_renderer": "R", "webgl_extensions": 29,
                                 "iframe_differs_from_top": []}
    assert "data:image/png" not in json.dumps(result), "the bundle itself is not returned"


async def test_unknown_sections_are_rejected(monkeypatch):
    monkeypatch.setattr(jsvmp, "browser_manager", _manager(_Page({})))
    result = await jsvmp.compare_env(output_path="x.json", sections=["navigator", "fonts"])
    assert "fonts" in result["error"]
