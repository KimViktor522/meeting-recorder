"""Составление протокола: OpenAI-совместимый API (Z.ai, OpenAI, OpenRouter, Ollama, свой URL), Anthropic API, Claude Code CLI."""
import json
import shutil
import subprocess
import urllib.error
import urllib.request

from . import settings


def _post(url, headers, body, timeout=600):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None


def complete(cfg, instruction, text, timeout=600):
    """Ответ модели на `instruction` + `text` (транскрипт). Бросает RuntimeError с понятным текстом."""
    p = cfg["protocol"]
    provider, key = p["provider"], settings.api_key(cfg)
    user = instruction + ("\n\n---\n" + text if text else "")
    if provider == "claude_cli":
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("Claude Code CLI not found in PATH")
        r = subprocess.run([exe, "-p", instruction], input=text, capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout * 3, creationflags=0x08000000)
        if r.returncode or not r.stdout.strip():
            raise RuntimeError(f"claude rc={r.returncode}: {(r.stderr or r.stdout)[:300]}")
        return r.stdout.strip()
    if provider == "none":
        raise RuntimeError("protocol provider is not configured")
    if not p.get("model"):
        raise RuntimeError("model is not set")
    if provider == "anthropic":
        if not key:
            raise RuntimeError("API key is not set")
        base = (p.get("base_url") or settings.PRESETS["anthropic"][0]).rstrip("/")
        out = _post(base + "/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"},
                    {"model": p["model"], "max_tokens": 4096, "messages": [{"role": "user", "content": user}]}, timeout)
        return "".join(b.get("text", "") for b in out["content"]).strip()
    base = (p.get("base_url") or settings.PRESETS.get(provider, ("",))[0]).rstrip("/")
    if not base:
        raise RuntimeError("base URL is not set")
    headers = {"Authorization": "Bearer " + key} if key else {}  # у локального Ollama ключа нет
    if not key and provider not in ("ollama", "custom"):
        raise RuntimeError("API key is not set")
    out = _post(base + "/chat/completions", headers, {"model": p["model"], "messages": [{"role": "user", "content": user}]}, timeout)
    return out["choices"][0]["message"]["content"].strip()


def test(cfg):
    """(ok, сообщение) для кнопки «Проверить»."""
    try:
        return True, complete(cfg, "Reply with the single word: OK", "", timeout=60)[:80]
    except Exception as e:
        return False, str(e)[:300]
