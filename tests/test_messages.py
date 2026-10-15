import pytest

from jobbot.whatsapp import messages as m


def test_clip_keeps_short_text_and_collapses_whitespace():
    assert m.clip("  hello   world ", 20) == "hello world"


def test_clip_truncates_with_ellipsis_at_limit():
    out = m.clip("a" * 50, 20)
    assert len(out) == 20 and out.endswith("…")


def test_button_message_enforces_whatsapp_limits():
    buttons = [m.Button("id:" + "x" * 300, "A very long button title indeed")]
    p = m.button_message("1555", "body", buttons, header="h" * 100, footer="f" * 100)
    b = p["interactive"]["action"]["buttons"][0]["reply"]
    assert len(b["id"]) <= m.ID_MAX
    assert len(b["title"]) <= m.BUTTON_TITLE_MAX
    assert len(p["interactive"]["header"]["text"]) <= m.HEADER_MAX
    assert len(p["interactive"]["footer"]["text"]) <= m.FOOTER_MAX


def test_button_message_rejects_too_many_buttons():
    with pytest.raises(ValueError):
        m.button_message("1555", "body", [m.Button(str(i), str(i)) for i in range(4)])


def test_list_message_limits_rows_and_titles():
    rows = [m.Row(f"r{i}", "t" * 40, "d" * 100) for i in range(10)]
    p = m.list_message("1555", "body", "Open this long button label", rows)
    section = p["interactive"]["action"]["sections"][0]
    assert len(section["rows"]) == 10
    assert all(len(r["title"]) <= m.LIST_ROW_TITLE_MAX for r in section["rows"])
    assert all(len(r["description"]) <= m.LIST_ROW_DESC_MAX for r in section["rows"])
    assert len(p["interactive"]["action"]["button"]) <= m.LIST_BUTTON_MAX
    with pytest.raises(ValueError):
        m.list_message("1555", "body", "Open", rows + [m.Row("x", "x")])


def test_template_message_shape():
    p = m.template_message("1555", "job_alert", "en", ["Asha", "Driver"])
    assert p["type"] == "template"
    assert p["template"]["name"] == "job_alert"
    assert p["template"]["language"] == {"code": "en"}
    assert [x["text"] for x in p["template"]["components"][0]["parameters"]] == ["Asha", "Driver"]
