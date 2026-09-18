"""Saver — обёртка над Anthropic API, которая собирает запрос экономно,
но никогда не в ущерб полноте данных.

Порядок сборки следует порядку рендера запроса (tools -> system -> messages)
и идёт от стабильного к изменчивому, чтобы работал кэш префикса:

    system[0]  правила            — не меняются никогда
    system[1]  файлы              — стабильны в рамках сессии   <- брейкпоинт
    system[2]  сводка + пины      — меняются редко              <- брейкпоинт
    messages   последние ходы     — растут                      <- брейкпоинт
    messages   новый вопрос       — меняется всегда (вне кэша)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from . import cache, guard, history, ledger, store
from .files import load as load_file, render as render_file

DEFAULT_MODEL = "claude-opus-5"
HELPER_MODEL = "claude-haiku-4-5"   # только внутренняя механика: сводки, классификация

MODES = {
    "economy": {"effort": "low",  "file_level": "index",    "max_tokens": 4000},
    "normal":  {"effort": "high", "file_level": "targeted", "max_tokens": 16000},
    "full":    {"effort": "max",  "file_level": "full",     "max_tokens": 16000},
}

BASE_SYSTEM = "Ты помогаешь работать с документами и данными. Отвечай по существу, без воды."


@dataclass
class Answer:
    text: str
    mode: str
    model: str
    escalated: bool = False
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    baseline_input: int = 0

    @property
    def saved_pct(self) -> float:
        actual = ledger.cost_usd(self.model, self.input_tokens, self.output_tokens,
                                 self.cache_read, self.cache_write)
        base = ledger.cost_usd(self.model, self.baseline_input, self.output_tokens)
        return (1 - actual / base) * 100 if base else 0.0


class Saver:
    def __init__(self, mode: str = "normal", model: str = DEFAULT_MODEL,
                 session: str = "default", db=None, client=None,
                 exact_baseline: bool = True):
        if mode not in MODES:
            raise ValueError(f"режим должен быть одним из {list(MODES)}")
        self.mode = mode
        self.model = model
        self.session = session
        self.conn = store.connect(db)
        self.client = client or anthropic.Anthropic()
        self.exact_baseline = exact_baseline
        self._requests = 0

    # ------------------------------------------------------------ сборка

    def _summarizer(self):
        def run(prompt: str) -> str:
            resp = self.client.messages.create(
                model=HELPER_MODEL, max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in resp.content if b.type == "text")
        return run

    def _file_text(self, paths: list[str], level: str, question: str) -> tuple[str, list[str]]:
        parts, warnings = [], []
        for path in paths:
            try:
                doc = load_file(self.conn, path)
            except (FileNotFoundError, ValueError) as exc:
                warnings.append(f"{path}: {exc}")
                continue
            if note := doc.meta.get("warning"):
                warnings.append(f"{Path(path).name}: {note}")
            parts.append(render_file(doc, level, question))
        return "\n\n".join(parts), warnings

    def _build(self, question: str, paths: list[str], mode: str, extra_system: str):
        cfg = MODES[mode]
        file_text, warnings = self._file_text(paths, cfg["file_level"], question)
        summary, pins, turns = history.pack(
            self.conn, self.session, mode, summarizer=self._summarizer())

        memory = "\n\n".join(p for p in (summary, pins) if p)
        rules = "\n\n".join(p for p in (BASE_SYSTEM, extra_system, guard.SYSTEM_RULES) if p)

        long_session = len(turns) > 10
        system_blocks, cache_warnings = cache.build_system(
            [("правила", rules), ("файлы", file_text), ("память", memory)],
            ttl_1h=long_session)
        warnings.extend(cache_warnings)

        messages = history.to_messages(turns) + [{"role": "user", "content": question}]
        messages = cache.mark_history(messages, ttl_1h=long_session)
        context_text = "\n".join([rules, file_text, memory] + [t["content"] for t in turns])
        return system_blocks, messages, context_text, cfg, warnings

    def _baseline_tokens(self, question: str, paths: list[str], extra_system: str) -> int:
        """Сколько стоил бы тот же запрос без оптимизации: вся история дословно,
        все файлы целиком, без кэша."""
        turns = history.all_turns(self.conn, self.session)
        file_text, _ = self._file_text(paths, "full", question)
        system = "\n\n".join(p for p in (BASE_SYSTEM, extra_system, file_text) if p)
        messages = history.to_messages(turns) + [{"role": "user", "content": question}]
        if not self.exact_baseline:
            return cache.est_tokens(system + "".join(m["content"] for m in messages))
        try:
            return self.client.messages.count_tokens(
                model=self.model, system=system, messages=messages).input_tokens
        except anthropic.APIError:
            return cache.est_tokens(system + "".join(m["content"] for m in messages))

    # ------------------------------------------------------------ запрос

    def _call(self, system_blocks, messages, cfg):
        return self.client.messages.create(
            model=self.model,
            max_tokens=cfg["max_tokens"],
            thinking={"type": "adaptive"},
            output_config={"effort": cfg["effort"]},
            system=system_blocks,
            messages=messages,
        )

    def ask(self, question: str, files: list[str] | None = None,
            system: str = "") -> Answer:
        paths = list(files or [])

        # Жёсткий override: на расчётах, договорах и сверках экономия запрещена
        # независимо от настройки режима.
        mode = "full" if guard.forces_full_context(question) else self.mode
        arithmetic = guard.forces_full_context(question)

        baseline = self._baseline_tokens(question, paths, system)
        system_blocks, messages, context_text, cfg, warnings = self._build(
            question, paths, mode, system)

        response = self._call(system_blocks, messages, cfg)
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        escalated = False

        # Эскалация: модель сказала, что данных мало, либо в ответе всплыли
        # числа, которых в контексте не было. Оба случая — повтор с полным
        # контекстом. Вниз по лестнице не спускаемся никогда.
        problems = guard.verify(text, context_text, arithmetic)
        if problems and mode == "full":
            warnings.extend(problems)

        if (guard.INSUFFICIENT in text or problems) and mode != "full":
            escalated = True
            warnings.append("контекст расширен до полного: "
                            + (problems[0] if problems else "модель сообщила о нехватке данных"))
            mode = "full"
            system_blocks, messages, context_text, cfg, more = self._build(
                question, paths, mode, system)
            warnings.extend(more)
            response = self._call(system_blocks, messages, cfg)
            text = "".join(b.text for b in response.content if b.type == "text")
            usage = ledger.Entry(  # суммируем оба вызова: платим за оба
                session=self.session, mode=mode, model=self.model,
                input_tokens=usage.input_tokens + response.usage.input_tokens,
                output_tokens=usage.output_tokens + response.usage.output_tokens,
                cache_read=(usage.cache_read_input_tokens or 0)
                           + (response.usage.cache_read_input_tokens or 0),
                cache_write=(usage.cache_creation_input_tokens or 0)
                            + (response.usage.cache_creation_input_tokens or 0),
            )
        else:
            usage = ledger.Entry(
                session=self.session, mode=mode, model=self.model,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read=usage.cache_read_input_tokens or 0,
                cache_write=usage.cache_creation_input_tokens or 0,
            )

        text = text.replace(guard.INSUFFICIENT, "").strip()
        if hint := cache.check_hits(response.usage, self._requests):
            warnings.append(hint)
        self._requests += 1

        history.append_turn(self.conn, self.session, "user", question)
        history.append_turn(self.conn, self.session, "assistant", text)

        usage.baseline_input = baseline
        usage.escalated = escalated
        ledger.record(self.conn, usage)

        return Answer(text=text, mode=mode, model=self.model, escalated=escalated,
                      warnings=warnings, input_tokens=usage.input_tokens,
                      output_tokens=usage.output_tokens, cache_read=usage.cache_read,
                      cache_write=usage.cache_write, baseline_input=baseline)
