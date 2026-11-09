from datetime import date

import httpx
import pytest

from app import parser
from app.parser import parse_with_rules

TODAY = date(2026, 11, 9)  # a Monday


@pytest.mark.parametrize(
    ("text", "start", "end", "leave_type"),
    [
        ("Tomorrow off please", date(2026, 11, 10), date(2026, 11, 10), "annual"),
        ("next Monday to Wednesday", date(2026, 11, 16), date(2026, 11, 18), "annual"),
        ("Friday", date(2026, 11, 13), date(2026, 11, 13), "annual"),
        ("14.12.2026 - 18.12.2026 izin", date(2026, 12, 14), date(2026, 12, 18), "annual"),
        ("haftaya pazartesi 3 gün izin", date(2026, 11, 16), date(2026, 11, 18), "annual"),
        ("Yarın hastayım, doktora gideceğim", date(2026, 11, 10), date(2026, 11, 10), "sick"),
        (
            "22 Aralık ile 24 Aralık arası ücretsiz izin",
            date(2026, 12, 22),
            date(2026, 12, 24),
            "unpaid",
        ),
        ("5 January", date(2027, 1, 5), date(2027, 1, 5), "annual"),
        ("Friday 3 days", date(2026, 11, 13), date(2026, 11, 17), "annual"),
    ],
)
def test_rule_parser(text, start, end, leave_type):
    draft = parse_with_rules(text, TODAY)
    assert (draft.start_date, draft.end_date, draft.leave_type) == (start, end, leave_type)
    assert draft.source == "rules"


def test_rule_parser_extracts_reason():
    assert parse_with_rules("Friday off because of a wedding", TODAY).reason == "a wedding"


def test_rule_parser_without_date_raises():
    with pytest.raises(ValueError):
        parse_with_rules("I am tired", TODAY)


class FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_llm_output_is_used_when_valid(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: FakeResponse(
            '{"leave_type": "annual", "start_date": "2026-11-20", "end_date": "2026-11-20", "reason": "moving"}'
        ),
    )
    draft = parser.parse("the friday after next, moving house", TODAY)
    assert draft.source == "llm"
    assert draft.start_date == date(2026, 11, 20)


@pytest.mark.parametrize(
    "bad_content",
    [
        "not json",
        '{"leave_type": "vacation"}',
        '{"leave_type": "annual", "start_date": "2026-11-20", "end_date": "2026-11-10"}',
    ],
)
def test_bad_llm_output_falls_back_to_rules(monkeypatch, bad_content):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(bad_content))
    draft = parser.parse("Tomorrow off", TODAY)
    assert draft.source == "rules"
    assert draft.start_date == date(2026, 11, 10)


def test_llm_network_error_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def boom(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", boom)
    assert parser.parse("Tomorrow off", TODAY).source == "rules"
