"""Post messages to Slack channels (bot token or incoming webhook)."""

from __future__ import annotations

import os
import re

import httpx

# CLI Market workspace — override via env if channels move
DEFAULT_CHANNEL_PUBLICACIONES = "C0B6ZJ1B9B8"  # publicaciones redes
DEFAULT_CHANNEL_BITACORA = "C0B6V3Y9ZSP"  # bitácora producto
DEFAULT_CHANNEL_REVISIONES_CURSOR = "C0B723TQS78"  # revisiones Cursor / Cloud Agent
DEFAULT_CHANNEL_CLI_MARKET_PRO = "C0B90LCEK0V"  # #suscripciones-cli-pro
DEFAULT_CHANNEL_FUNNEL = "C0B9G3T0T0A"  # funnel-cli-market
COMMAND_CONTROL_CHANNEL_NAME = "command-control-cli-market"
CLI_MARKET_PRO_CHANNEL_NAME = "suscripciones-cli-pro"
FUNNEL_CHANNEL_NAME = "funnel-cli-market"

MAX_SLACK_TEXT = 3900


def channel_publicaciones() -> str:
    return os.getenv("SLACK_CHANNEL_PUBLICACIONES", DEFAULT_CHANNEL_PUBLICACIONES)


def channel_bitacora() -> str:
    return os.getenv("SLACK_CHANNEL_BITACORA", DEFAULT_CHANNEL_BITACORA)


def channel_revisiones_cursor() -> str:
    return os.getenv(
        "SLACK_CHANNEL_REVISIONES_CURSOR", DEFAULT_CHANNEL_REVISIONES_CURSOR
    )


def _resolve_channel_by_name(token: str, name: str) -> str | None:
    """Lookup channel ID by name (needs channels:read or groups:read)."""
    cursor = ""
    for _ in range(20):
        payload: dict = {
            "types": "public_channel,private_channel",
            "limit": 200,
            "exclude_archived": True,
        }
        if cursor:
            payload["cursor"] = cursor
        r = httpx.post(
            "https://slack.com/api/conversations.list",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            return None
        for ch in body.get("channels", []):
            if ch.get("name") == name:
                return ch.get("id")
        cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            break
    return None


def channel_cli_market_pro() -> str:
    explicit = (
        os.getenv("SLACK_CHANNEL_CLI_MARKET_PRO", "").strip()
        or DEFAULT_CHANNEL_CLI_MARKET_PRO
    )
    if explicit:
        return explicit
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if token:
        resolved = _resolve_channel_by_name(token, CLI_MARKET_PRO_CHANNEL_NAME)
        if resolved:
            return resolved
    raise ValueError(
        "Subscriptions channel not configured. Set SLACK_CHANNEL_CLI_MARKET_PRO "
        f"to the ID of #{CLI_MARKET_PRO_CHANNEL_NAME}, or grant channels:read to the bot."
    )


def channel_funnel() -> str:
    explicit = (
        os.getenv("SLACK_CHANNEL_FUNNEL", "").strip() or DEFAULT_CHANNEL_FUNNEL
    )
    if explicit:
        return explicit
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if token:
        resolved = _resolve_channel_by_name(token, FUNNEL_CHANNEL_NAME)
        if resolved:
            return resolved
    raise ValueError(
        "Funnel channel not configured. Set SLACK_CHANNEL_FUNNEL "
        f"to the ID of #{FUNNEL_CHANNEL_NAME}, or grant channels:read to the bot."
    )


def channel_command_control() -> str:
    explicit = os.getenv("SLACK_CHANNEL_COMMAND_CONTROL", "").strip()
    if explicit:
        return explicit
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if token:
        resolved = _resolve_channel_by_name(token, COMMAND_CONTROL_CHANNEL_NAME)
        if resolved:
            return resolved
    raise ValueError(
        "Command & Control channel not configured. Set SLACK_CHANNEL_COMMAND_CONTROL "
        f"to the ID of #{COMMAND_CONTROL_CHANNEL_NAME}, or grant channels:read to the bot."
    )


def _chunk_text(text: str, limit: int = MAX_SLACK_TEXT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit and buf:
            chunks.append("".join(buf))
            buf = []
            size = 0
        buf.append(line)
        size += len(line)
    if buf:
        chunks.append("".join(buf))
    return chunks


def _md_to_slack_plain(text: str) -> str:
    """Lightweight markdown → Slack mrkdwn (non-fenced regions only)."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("### "):
            out.append(f"*{line[4:].strip()}*")
        elif line.startswith("## "):
            out.append(f"*{line[3:].strip()}*")
        elif line.startswith("# "):
            out.append(f"*{line[2:].strip()}*")
        elif line.startswith("|") and "---" not in line:
            out.append(line.replace("|", " ").strip())
        else:
            out.append(line.replace("**", "*"))
    return "\n".join(out)


def _md_to_slack(text: str) -> str:
    """Markdown → Slack mrkdwn; fenced ``` blocks pass through for copy-paste."""
    parts = re.split(r"(```[\s\S]*?```)", text)
    out: list[str] = []
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            out.append(part)
        else:
            transformed = _md_to_slack_plain(part)
            if transformed:
                out.append(transformed)
    return "\n".join(out)


def post_via_webhook(webhook_url: str, text: str) -> None:
    for chunk in _chunk_text(text):
        r = httpx.post(webhook_url, json={"text": chunk}, timeout=15)
        r.raise_for_status()


def post_via_bot(token: str, channel: str, text: str) -> None:
    slack_text = _md_to_slack(text)
    chunks = _chunk_text(slack_text)
    for i, chunk in enumerate(chunks):
        prefix = f"_(parte {i + 1}/{len(chunks)})_\n\n" if len(chunks) > 1 else ""
        r = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": prefix + chunk, "mrkdwn": True},
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"Slack API error: {body.get('error', body)}")


def post_blocks_via_bot(token: str, channel: str, *, text: str, blocks: list) -> None:
    r = httpx.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "channel": channel,
            "text": text,
            "blocks": blocks,
            "mrkdwn": True,
        },
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack API error: {body.get('error', body)}")


def deliver(
    text: str,
    *,
    channel: str,
    webhook_url: str = "",
    bot_token: str = "",
) -> None:
    """Send text to a channel using bot token (preferred) or webhook."""
    token = bot_token or os.getenv("SLACK_BOT_TOKEN", "")
    hook = webhook_url.strip()

    if token:
        post_via_bot(token, channel, text)
        return
    if hook:
        post_via_webhook(hook, text)
        return
    raise ValueError(
        "Slack not configured: set SLACK_BOT_TOKEN or a channel-specific webhook URL"
    )


def deliver_to_bitacora(text: str) -> None:
    deliver(
        text,
        channel=channel_bitacora(),
        webhook_url=os.getenv("SLACK_WEBHOOK_BITACORA", ""),
    )


def deliver_to_publicaciones(text: str) -> None:
    deliver(
        text,
        channel=channel_publicaciones(),
        webhook_url=os.getenv("SLACK_WEBHOOK_PUBLICACIONES", ""),
    )


def deliver_to_revisiones_cursor(text: str) -> None:
    deliver(
        text,
        channel=channel_revisiones_cursor(),
        webhook_url=os.getenv("SLACK_WEBHOOK_REVISIONES_CURSOR", ""),
    )


def deliver_to_command_control(text: str) -> None:
    deliver(
        text,
        channel=channel_command_control(),
        webhook_url=os.getenv("SLACK_WEBHOOK_COMMAND_CONTROL", ""),
    )


def deliver_to_funnel(text: str) -> None:
    deliver(
        text,
        channel=channel_funnel(),
        webhook_url=os.getenv("SLACK_WEBHOOK_FUNNEL", ""),
    )


def deliver_to_cli_market_pro(text: str, *, blocks: list | None = None) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    hook = os.getenv("SLACK_WEBHOOK_CLI_MARKET_PRO", "").strip()
    channel = channel_cli_market_pro()
    if blocks and token:
        post_blocks_via_bot(token, channel, text=text, blocks=blocks)
        return
    deliver(
        text,
        channel=channel,
        webhook_url=hook,
        bot_token=token,
    )
