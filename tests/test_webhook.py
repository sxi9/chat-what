import hashlib
import hmac
import json

from jobbot.whatsapp.webhook import parse_webhook, verify_signature


def make_payload(messages=None, statuses=None, phone="15550001111", name="Asha"):
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "1"},
             "contacts": [{"profile": {"name": name}, "wa_id": phone}]}
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "0", "changes": [{"field": "messages", "value": value}]}]}


def text_msg(body, phone="15550001111", mid="wamid.1"):
    return {"from": phone, "id": mid, "timestamp": "1700000000", "type": "text", "text": {"body": body}}


def button_msg(bid, title, phone="15550001111", mid="wamid.2"):
    return {"from": phone, "id": mid, "timestamp": "1700000000", "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": bid, "title": title}}}


def list_msg(rid, title, phone="15550001111", mid="wamid.3"):
    return {"from": phone, "id": mid, "timestamp": "1700000000", "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": rid, "title": title}}}


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_parse_text_button_list_and_status():
    payload = make_payload(
        messages=[text_msg(" hello "), button_msg("menu:jobs", "Find jobs"), list_msg("job:3", "Driver"),
                  {"from": "15550001111", "id": "wamid.4", "type": "image", "image": {}}],
        statuses=[{"id": "wamid.out", "status": "failed", "errors": [{"code": 131047, "title": "Re-engagement"}]}],
    )
    inbound, statuses = parse_webhook(payload)
    assert [i.kind for i in inbound] == ["text", "button", "list", "other"]
    assert inbound[0].text == "hello" and inbound[0].profile_name == "Asha"
    assert inbound[1].text == "menu:jobs" and inbound[1].label == "Find jobs"
    assert inbound[2].text == "job:3"
    assert statuses[0].status == "failed" and "131047" in statuses[0].error


def test_signature_verification():
    body = b'{"x": 1}'
    assert verify_signature("secret", body, sign("secret", body))
    assert not verify_signature("secret", body, sign("other", body))
    assert not verify_signature("secret", body, None)
    assert verify_signature("", body, None)  # disabled when no secret configured


def test_get_webhook_verification(client):
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token",
                                       "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"
    r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"})
    assert r.status_code == 403


def test_post_webhook_requires_valid_signature(client):
    body = json.dumps(make_payload(messages=[text_msg("hi")])).encode()
    assert client.post("/webhook", content=body).status_code == 403
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign("test-app-secret", body)})
    assert r.status_code == 200 and r.json()["handled"] == 1


def test_post_webhook_ignores_duplicate_delivery(client, sender):
    body = json.dumps(make_payload(messages=[text_msg("hi", mid="wamid.dup")])).encode()
    headers = {"X-Hub-Signature-256": sign("test-app-secret", body)}
    assert client.post("/webhook", content=body, headers=headers).json()["handled"] == 1
    assert client.post("/webhook", content=body, headers=headers).json()["handled"] == 0
    assert len(sender.sent) == 1


def test_status_update_recorded_on_outbound(client, sender):
    # First trigger an outbound message, then report its delivery status back.
    body = json.dumps(make_payload(messages=[text_msg("hi", mid="wamid.a")])).encode()
    client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign("test-app-secret", body)})
    status_body = json.dumps(make_payload(statuses=[{"id": "wamid.test.1", "status": "read"}])).encode()
    r = client.post("/webhook", content=status_body,
                    headers={"X-Hub-Signature-256": sign("test-app-secret", status_body)})
    assert r.json()["statuses"] == 1
    from jobbot import db as dbmod
    from jobbot.models import OutboundMessage
    with dbmod.session_scope() as db:
        row = db.query(OutboundMessage).filter_by(wa_message_id="wamid.test.1").one()
        assert row.delivery_status == "read"
