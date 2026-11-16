from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PROVIDERS: dict[str, dict[str, str]] = {
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "image_model": "cogview-3-flash",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "image_model": "",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "openai_compatible": {
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "image_model": "",
        "key_url": "",
    },
}

DEFAULT_AI_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "provider": "zhipu",
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "model": "glm-4-flash",
    "api_key": "",
    "temperature": 0.8,
    "max_chars": 32,
    "timeout": 20,
    "persona": "轻松自然、像熟人随口聊天，不要客套和营销腔",
    "image_model": "",
}

_SENTENCE_BREAK = re.compile(r"[\r\n]+")
_QUOTE_WRAP = re.compile(r'^[\s"“”\'‘’「」『』]+|[\s"“”\'‘’「」『』]+$')


class AIError(RuntimeError):
    """Raised when the model call fails or returns unusable text."""


def settings_with_defaults(raw: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_AI_SETTINGS)
    if raw:
        merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def sanitize(text: str, max_chars: int) -> str:
    """Turn a model reply into one short chat line, or raise when unusable."""
    value = _SENTENCE_BREAK.sub(" ", text or "")
    value = re.sub(r"^\s*(消息|回复|文案)[:：]\s*", "", value)
    value = _QUOTE_WRAP.sub("", value).strip()
    value = re.sub(r"\s{2,}", " ", value)
    if not value:
        raise AIError("模型返回空内容")
    if "{" in value and "}" in value:
        raise AIError("模型返回了未替换的模板变量")
    if len(value) > max_chars:
        value = value[:max_chars].rstrip()
    return value


def build_prompt(
    settings: dict[str, Any],
    nickname: str,
    template: str,
    *,
    when: datetime,
    recent: list[str] | None = None,
) -> list[dict[str, str]]:
    recent = [item for item in (recent or []) if item.strip()][-5:]
    system = (
        "你在帮用户给抖音好友发一句续火花的消息。要求：只输出这一条消息本身，"
        "不要解释、不要引号、不要编号；口语化、自然、不超过{max_chars}个字；"
        "语气：{persona}；不要提“火花”“续火花”这类词，不要重复最近的句子。"
    ).format(max_chars=settings.get("max_chars", 32), persona=settings.get("persona", "轻松自然"))
    parts = [
        f"好友昵称：{nickname}",
        f"现在时间：{when.strftime('%Y-%m-%d %H:%M')}（{_weekday_cn(when)}）",
        f"参考风格：{template}" if template.strip() else "参考风格：无",
        "最近已发送：" + " / ".join(recent) if recent else "最近已发送：无",
        "请输出一条新的消息。",
    ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(parts)},
    ]


def _weekday_cn(when: datetime) -> str:
    return ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[when.weekday()]


def _chat(settings: dict[str, Any], messages: list[dict[str, str]], *, max_tokens: int, opener: Any = None) -> str:
    """POST one chat completion and return the raw assistant text."""
    cfg = settings_with_defaults(settings)
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        raise AIError("未配置 AI API Key")
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise AIError("AI 接口地址无效")
    url = f"{base}/chat/completions"
    payload = {
        "model": cfg.get("model") or "glm-4-flash",
        "messages": messages,
        "temperature": float(cfg.get("temperature", 0.8)),
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    timeout = float(cfg.get("timeout", 20))
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        hint = ""
        try:
            hint = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            hint = ""
        raise AIError(f"AI 接口返回 HTTP {exc.code}：{hint}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise AIError("AI 接口请求超时") from exc
        raise AIError(f"无法连接 AI 接口：{exc.reason}") from exc
    except TimeoutError as exc:
        raise AIError("AI 接口请求超时") from exc
    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AIError(f"AI 接口返回格式无法解析：{raw[:160]}") from exc
    return str(content or "").strip()


def generate_message(
    settings: dict[str, Any],
    nickname: str,
    template: str,
    *,
    when: datetime,
    recent: list[str] | None = None,
    opener: Any = None,
) -> str:
    """Call an OpenAI-compatible chat endpoint and return one short chat line."""
    cfg = settings_with_defaults(settings)
    prompt = build_prompt(cfg, nickname, template, when=when, recent=recent)
    raw = _chat(cfg, prompt, max_tokens=128, opener=opener)
    return sanitize(raw, int(cfg.get("max_chars", 32)))


def chat_text(settings: dict[str, Any], system: str, user: str, *, max_tokens: int = 400, opener: Any = None) -> str:
    """Free-form chat completion used for summaries and reports."""
    return _chat(settings, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                 max_tokens=max_tokens, opener=opener)


def describe_provider(name: str) -> str:
    return PROVIDERS.get(name, PROVIDERS["openai_compatible"])["label"]


STICKER_STYLE = (
    "抖音聊天里用的表情包贴图。正方形构图，主体居中、占比大，轮廓清楚；"
    "扁平插画风格，颜色明亮干净，背景为纯白或浅色；"
    "画面里不要出现任何文字、水印、二维码；不要写实照片感。"
)


def sticker_prompt(subject: str) -> str:
    """Turn a short idea from the user into a full image prompt."""
    subject = (subject or "").strip() or "一只举着咖啡、笑得有点累的小猫"
    return f"{subject}。{STICKER_STYLE}"


def normalize_image_model(cfg: dict[str, Any]) -> tuple[str, bool]:
    """Return (model name, was_corrected).

    A接口 address pasted into the「图像模型」field is a common mistake; fall back
    to the provider preset instead of shipping a URL as the model param.
    """
    value = str(cfg.get("image_model") or "").strip()
    base = str(cfg.get("base_url") or "").rstrip("/")
    preset = PROVIDERS.get(str(cfg.get("provider") or ""), {})
    fallback = str(preset.get("image_model") or "")
    looks_like_url = (
        "://" in value
        or value.startswith(("http.", "www."))
        or (base and value.rstrip("/") == base)
    )
    if looks_like_url:
        return fallback, True
    if not value:
        return fallback, False
    return value, False


def generate_image(settings: dict[str, Any], prompt: str, *, opener: Any = None) -> bytes:
    """Call an OpenAI-compatible `/images/generations` endpoint and return PNG bytes."""
    cfg = settings_with_defaults(settings)
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        raise AIError("未配置 AI API Key")
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise AIError("AI 接口地址无效")
    model, corrected = normalize_image_model(cfg)
    if not model:
        if corrected:
            raise AIError(
                "「图像模型」里填的是接口地址，不是模型名。请改填模型名，例如智谱填 cogview-3-flash"
            )
        raise AIError("当前服务商没有配置图像模型，请在「自动化与 AI 设置」里填写图像模型名")
    payload = {"model": model, "prompt": prompt, "n": 1, "size": "1024x1024", "response_format": "url"}
    request = urllib.request.Request(
        f"{base}/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    timeout = float(cfg.get("timeout", 20)) * 3
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        hint = ""
        try:
            hint = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            hint = ""
        raise AIError(f"图像接口返回 HTTP {exc.code}：{hint}") from exc
    except urllib.error.URLError as exc:
        raise AIError(f"无法连接图像接口：{exc.reason}") from exc
    except TimeoutError as exc:
        raise AIError("图像接口请求超时") from exc
    try:
        item = json.loads(raw)["data"][0]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AIError(f"图像接口返回格式无法解析：{raw[:160]}") from exc
    if isinstance(item, dict) and item.get("b64_json"):
        import base64

        try:
            return base64.b64decode(str(item["b64_json"]))
        except Exception as exc:  # pragma: no cover - defensive
            raise AIError(f"图像数据解码失败：{exc}") from exc
    url = str(item.get("url") or "") if isinstance(item, dict) else ""
    if not url.startswith(("https://", "http://")):
        raise AIError("图像接口没有返回可用地址")
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - network
        raise AIError(f"下载生成的表情失败：{exc}") from exc


def generate_variants(
    settings: dict[str, Any],
    nickname: str,
    template: str,
    *,
    when: datetime,
    recent: list[str] | None = None,
    count: int = 3,
    opener: Any = None,
) -> list[str]:
    """Ask the model for several different one-liners (deduplicated)."""
    results: list[str] = []
    base = settings_with_defaults(settings)
    for index in range(max(1, min(5, count))):
        cfg = dict(base)
        cfg["temperature"] = min(1.5, float(base.get("temperature", 0.8)) + index * 0.15)
        try:
            text = generate_message(cfg, nickname, template, when=when, recent=(recent or []) + results, opener=opener)
        except AIError:
            if not results:
                raise
            continue
        if text not in results:
            results.append(text)
    return results


def apply_provider(settings: dict[str, Any], name: str) -> dict[str, Any]:
    """Switch base_url/model to the preset of `name` while keeping the key."""
    preset = PROVIDERS.get(name)
    merged = settings_with_defaults(settings)
    if preset:
        merged["provider"] = name
        merged["base_url"] = preset["base_url"]
        merged["model"] = preset["model"]
        merged["image_model"] = preset.get("image_model", "")
    return merged


QUOTE_SYSTEM = (    "你是一位说话很硬的教练。只输出一句 10 到 24 字的短句，语气干脆、强势、有命令感，"
    "像在催自己立刻干活；不要温柔安慰，不要鸡汤腔，不加引号、不加编号。"
    "第一行只写句子，第二行只写作者或“佚名”。不要解释，不要多余文字。"
)


def suggest_quote(
    settings: dict[str, Any], *, recent: list[str] | tuple[str, ...] = (), opener: Any = None
) -> tuple[str, str]:
    """Ask the configured chat model for one fresh inspirational line; returns (text, author)."""
    cfg = settings_with_defaults(settings)
    cfg["timeout"] = max(float(cfg.get("timeout") or 20), 45)
    user = "写一句够狠、能催人立刻动手的短句。"
    known = [line.strip() for line in recent if line and line.strip()]
    if known:
        user += "不要重复这些：" + "；".join(known[:8])
    raw = chat_text(cfg, QUOTE_SYSTEM, user, max_tokens=60, opener=opener)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        raise AIError("AI 没有返回可用的金句")
    text = lines[0].strip().lstrip("0123456789.、-— ").strip().strip("“”\"'").strip()
    author = lines[1].strip("—-— 　") if len(lines) > 1 else "佚名"
    text = text[:40].strip()
    if not text:
        raise AIError("AI 返回的句子是空的")
    return text, author or "佚名"


def probe_chat(settings: dict[str, Any], *, opener: Any = None, timeout: float = 10.0) -> dict[str, Any]:
    """One tiny request to see whether the configured chat endpoint is actually reachable."""
    cfg = settings_with_defaults(settings)
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        return {"ok": False, "detail": "还没填 API Key", "hint": "到「AI 文案」里填 Key 再测"}
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base.startswith(("https://", "http://")):
        return {"ok": False, "detail": f"接口地址无效：{base or '空'}", "hint": "地址要以 https:// 开头"}
    model = str(cfg.get("model") or "glm-4-flash")
    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 4,
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(request, timeout=timeout) as response:
            response.read()
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:160]
        except Exception:
            body = ""
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": elapsed,
            "url": url,
            "model": model,
            "detail": f"HTTP {exc.code}：{body or '接口拒绝请求'}",
            "hint": "HTTP 401/403 多半是 Key 不对；404 检查接口地址；400 检查模型名",
        }
    except urllib.error.URLError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        reason = exc.reason
        timed_out = isinstance(reason, (TimeoutError, socket.timeout))
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": elapsed,
            "url": url,
            "model": model,
            "detail": "连接超时，没等到响应" if timed_out else f"连不上接口：{reason}",
            "hint": "国内直连 api.openai.com 通常不通；换智谱 glm-4-flash、DeepSeek 或可用的中转地址",
        }
    except (TimeoutError, socket.timeout):
        elapsed = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": elapsed,
            "url": url,
            "model": model,
            "detail": "连接超时，没等到响应",
            "hint": "换国内可达的服务商，或给接口地址配好代理",
        }
    elapsed = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "status": status,
        "elapsed_ms": elapsed,
        "url": url,
        "model": model,
        "detail": f"{status} OK",
        "hint": "",
    }
