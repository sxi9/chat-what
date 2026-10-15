"""Help & FAQ module. Content is data, loaded from a JSON file, so non-developers can edit it.

File format: [{"id": "hours", "question": "Opening hours", "answer": "..."}, ...]
`id` must be short and url-safe; `question` is the list row title (24 chars max).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...whatsapp import messages as m
from ..context import Ctx
from . import MenuEntry, Module

BODY = "Pick a question and I'll answer right away."
EMPTY = "There are no help topics yet. Use Contact us to reach the team."
NOT_FOUND = "I don't have that topic any more. Here is the current list."


@dataclass(frozen=True)
class FaqItem:
    id: str
    question: str
    answer: str


class FaqModule(Module):
    key = "faq"
    entries = (MenuEntry("faq", "Help & FAQ", "Answers to common questions"),)
    commands = ("faq",)
    keywords = {"faq": "faq", "help me": "faq", "questions": "faq"}

    def __init__(self, items: list[FaqItem]):
        self.items = items[: m.LIST_ROWS_MAX]

    @classmethod
    def from_file(cls, path: str | Path) -> "FaqModule":
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls([FaqItem(str(i["id"]), str(i["question"]), str(i["answer"])) for i in raw])

    def open(self, ctx: Ctx, entry: str) -> None:
        self.show_list(ctx)

    def command(self, ctx: Ctx, action: str, arg: str) -> None:
        item = next((i for i in self.items if i.id == arg), None)
        if item is None:
            self.show_list(ctx, NOT_FOUND)
            return
        ctx.set_state("MENU")
        ctx.buttons(f"*{item.question}*\n\n{item.answer}",
                    [m.Button("menu:faq", "More questions"), m.Button("menu:contact", "Contact us"),
                     m.Button("menu:main", "Main menu")], kind="faq_answer")

    def show_list(self, ctx: Ctx, preface: str | None = None) -> None:
        ctx.set_state("MENU")
        if not self.items:
            ctx.buttons(EMPTY, [m.Button("menu:contact", "Contact us"), m.Button("menu:main", "Main menu")],
                        kind="faq_empty")
            return
        body = f"{preface}\n\n{BODY}" if preface else BODY
        rows = [m.Row(f"faq:{i.id}", i.question) for i in self.items]
        ctx.list(body, "See questions", rows, section_title="Help topics", header="Help & FAQ", kind="faq_list")
