"""AnkiConnect 客户端（localhost HTTP API）。

负责：确保 deck 与 InterviewQA note type 存在，addNotes 写卡。
localhost 调用同样绕过系统代理。
"""
from __future__ import annotations

import httpx

from .config import load_config

# InterviewQA Note Type 字段（§4.3）
FIELDS = ["Question", "OralAnswer", "KeyPoints", "Cloze", "QID", "Topic"]


class AnkiError(RuntimeError):
    pass


class Anki:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or load_config()
        self.c = cfg["anki"]
        self.deck = self.c["deck"]
        self.model = self.c["note_type"]
        self._client = httpx.Client(timeout=30, trust_env=False)

    def _invoke(self, action: str, **params):
        resp = self._client.post(self.c["url"], json={
            "action": action, "version": 6, "params": params,
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise AnkiError(f"{action}: {data['error']}")
        return data["result"]

    def version(self) -> int:
        return self._invoke("version")

    def ensure_setup(self) -> None:
        """幂等地创建 deck 和 InterviewQA note type。"""
        if self.deck not in self._invoke("deckNames"):
            self._invoke("createDeck", deck=self.deck)
        if self.model not in self._invoke("modelNames"):
            self._invoke(
                "createModel",
                modelName=self.model,
                inOrderFields=FIELDS,
                isCloze=False,
                cardTemplates=[
                    {
                        "Name": "口述卡",
                        # 提示先开口说，再翻面看要点
                        "Front": "<div class='q'>{{Question}}</div>"
                                 "<div class='hint'>🗣️ 先开口讲 1-2 分钟，再翻面对照</div>",
                        "Back": "{{FrontSide}}<hr id='answer'>"
                                "<div class='oral'>{{OralAnswer}}</div>"
                                "{{#KeyPoints}}<hr><b>要点</b><div>{{KeyPoints}}</div>{{/KeyPoints}}"
                                "<div class='meta'>QID {{QID}} · {{Topic}}</div>",
                    }
                ],
                css=(
                    ".card{font-family:-apple-system,Segoe UI,sans-serif;font-size:18px;"
                    "line-height:1.6;color:#222;background:#fafafa;padding:16px;}"
                    ".q{font-weight:600;font-size:20px;}"
                    ".hint{color:#888;font-size:14px;margin-top:8px;}"
                    ".oral{white-space:pre-wrap;}"
                    ".meta{color:#aaa;font-size:12px;margin-top:12px;}"
                ),
            )

    CLOZE_MODEL = "InterviewCloze"

    def ensure_cloze(self) -> None:
        """幂等创建一个 cloze note type（用于关键数字/术语挖空卡）。"""
        if self.CLOZE_MODEL in self._invoke("modelNames"):
            return
        self._invoke(
            "createModel", modelName=self.CLOZE_MODEL,
            inOrderFields=["Text", "Extra", "QID", "Topic"], isCloze=True,
            cardTemplates=[{
                "Name": "Cloze",
                "Front": "{{cloze:Text}}",
                "Back": "{{cloze:Text}}<br>{{Extra}}"
                        "<div style='color:#aaa;font-size:12px;margin-top:8px'>QID {{QID}} · {{Topic}}</div>",
            }],
            css=(".card{font-family:-apple-system,Segoe UI,sans-serif;font-size:18px;"
                 "background:#fafafa;color:#222;padding:16px}.cloze{font-weight:700;color:#2563eb}"))

    def add_cloze(self, text: str, extra: str = "", qid: str = "", topic: str = "",
                  tags: list[str] | None = None) -> int:
        self.ensure_cloze()
        note = {"deckName": self.deck, "modelName": self.CLOZE_MODEL,
                "fields": {"Text": text, "Extra": extra, "QID": qid, "Topic": topic},
                "tags": tags or [], "options": {"allowDuplicate": True}}
        return self._invoke("addNote", note=note)

    def add_note(self, fields: dict, tags: list[str] | None = None) -> int:
        """写入一张 InterviewQA 卡，返回 note id。allowDuplicate 避免重复报错中断。"""
        note = {
            "deckName": self.deck,
            "modelName": self.model,
            "fields": {k: fields.get(k, "") for k in FIELDS},
            "tags": tags or [],
            "options": {"allowDuplicate": True},
        }
        return self._invoke("addNote", note=note)

    def close(self):
        self._client.close()
