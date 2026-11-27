"""Optional alert delivery; credentials are read afresh from the mounted Secret."""

import json
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from urllib import error, parse, request

ALERTS_CONFIG_FILE = os.getenv("ALERTS_CONFIG_FILE", "/etc/alerts/alerts.json")
_CHANNELS = ("slack", "teams", "email", "youtrack")


def load_config() -> dict:
    try:
        with open(ALERTS_CONFIG_FILE, encoding="utf-8") as handle:
            config = json.load(handle)
        return config if isinstance(config, dict) else {}
    except Exception:
        return {}


def _settings(config, channel):
    value = config.get(channel, {})
    return value if isinstance(value, dict) else {}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _active(config):
    slack, teams, email, youtrack = (_settings(config, c) for c in _CHANNELS)
    return {
        "slack": _text(slack.get("webhookUrl")),
        "teams": _text(teams.get("webhookUrl")),
        "email": bool(
            _text(email.get("smtpHost"))
            and _text(email.get("from"))
            and isinstance(email.get("to"), list)
            and any(_text(address) for address in email["to"])
        ),
        "youtrack": all(_text(youtrack.get(k)) for k in ("url", "token", "project")),
    }


def channels() -> dict:
    return _active(load_config())


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    return remaining


def _http(url, deadline, payload=None, token=None, read_json=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=body, headers=headers)
    with request.urlopen(req, timeout=_remaining(deadline)) as response:
        if read_json:
            return json.load(response)
    return None


def _email(config, title, description, deadline):
    message = EmailMessage()
    message["Subject"] = "[node-shutdown] " + title
    message["From"] = config["from"]
    recipients = [address for address in config["to"] if _text(address)]
    message["To"] = ", ".join(recipients)
    message.set_content(description)
    port = int(config.get("smtpPort", 587))
    context = ssl.create_default_context()
    smtp_type = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    kwargs = {"timeout": _remaining(deadline)}
    if port == 465:
        kwargs["context"] = context
    with smtp_type(config["smtpHost"], port, **kwargs) as smtp:
        if port != 465 and config.get("startTls", True):
            smtp.sock.settimeout(_remaining(deadline))
            smtp.starttls(context=context)
        if config.get("username"):
            smtp.sock.settimeout(_remaining(deadline))
            smtp.login(config["username"], config.get("password", ""))
        smtp.sock.settimeout(_remaining(deadline))
        if smtp.send_message(message, to_addrs=recipients):
            raise smtplib.SMTPException()


def _deliver(channel, config, title, lines, severity):
    deadline = time.monotonic() + 10
    description = "\n".join(lines)
    text = f"[{severity.upper()}] {title}\n{description}"
    if channel == "slack":
        _http(config["webhookUrl"], deadline, {"text": text})
    elif channel == "teams":
        _http(config["webhookUrl"], deadline, {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard", "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
                        {"type": "TextBlock", "text": text, "wrap": True},
                    ],
                },
            }],
        })
    elif channel == "email":
        _email(config, title, description, deadline)
    elif channel == "youtrack":
        base = config["url"].rstrip("/")
        token = config["token"]
        query = parse.urlencode({"fields": "id,shortName", "query": config["project"]})
        projects = _http(base + "/api/admin/projects?" + query, deadline,
                         token=token, read_json=True)
        project = next((p for p in projects if p.get("shortName") == config["project"]), None)
        if not project:
            raise ValueError()
        issue = _http(base + "/api/issues?fields=idReadable", deadline, {
            "project": {"id": project["id"]},
            "summary": "[node-shutdown] " + title,
            "description": description,
        }, token=token, read_json=True)
        if config.get("assignee"):
            _http(base + "/api/commands", deadline, {
                "query": "Assignee " + config["assignee"],
                "issues": [{"idReadable": issue["idReadable"]}],
            }, token=token)


def _safe_error(exc):
    # Transport exceptions can contain URLs, tokens, passwords or server echoes.
    if isinstance(exc, error.HTTPError):
        code = int(exc.code)
        exc.close()
        return f"HTTP {code}"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"SMTP {int(exc.smtp_code)}"
    if isinstance(exc, TimeoutError):
        return "Alert delivery timed out"
    return "Alert delivery failed; check channel configuration and connectivity"


def send(title: str, lines: list[str], severity: str = "warning") -> dict:
    results = {}
    try:
        config = load_config()
        for channel, active in _active(config).items():
            if not active:
                continue
            try:
                _deliver(channel, _settings(config, channel), title, lines, severity)
                results[channel] = "ok"
            except Exception as exc:
                results[channel] = _safe_error(exc)
    except Exception:
        # Alert failures must never interrupt shutdown or recovery.
        return results
    return results


def test(channel: str) -> dict:
    try:
        config = load_config()
        if channel not in _CHANNELS:
            return {"ok": False, "error": "Unknown alert channel"}
        if not _active(config)[channel]:
            return {"ok": False, "error": "Alert channel is not configured"}
        _deliver(channel, _settings(config, channel), "Tes notifikasi",
                 ["Pesan tes dari node-shutdown."], "info")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": _safe_error(exc)}
