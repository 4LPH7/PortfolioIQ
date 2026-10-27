"""
AI research committee: advisory text about one stock. It has no tools and no path to orders.

Pipeline after TradingAgents (analyst team -> bull/bear debate -> portfolio manager), with
investor personas after ai-hedge-fund. Prompts are written new for PortfolioIQ.

Every call uses structured output, the server-side refusal fallback, a hard budget that is
checked before the call (worst case) and settled after it (actual usage), and leaves an audit
record. Nothing here touches the database; runs.py persists what this returns.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from src.research.snapshot import SNAPSHOT_SCHEMA_VERSION, allowlist, check_citation, to_prompt_json

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PARALLEL = 3
DEBATE_ROUNDS = 2

# USD per million tokens: input, output, cache read, cache write (5-minute TTL)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5),
}
MAX_PRICE = tuple(max(p[i] for p in PRICES.values()) for i in range(4))

# stage kind -> (model slot, effort, max_tokens)
SETTINGS = {
    "analyst": ("quick", "low", 6000),
    "debate": ("quick", "medium", 8000),
    "pm": ("deep", "high", 10000),
}


# ── Output schemas ────────────────────────────────────────────


class Citation(BaseModel):
    snapshot_key: str = Field(description="Key path in the snapshot, e.g. price.return_3m")
    value: float
    unit: str


class AnalystView(BaseModel):
    stance: Literal["bullish", "neutral", "bearish"]
    confidence: int = Field(ge=0, le=100)
    summary: str
    key_points: list[str]
    citations: list[Citation]


class DebateTurn(BaseModel):
    argument: str
    citations: list[Citation]


class CommitteeDecision(BaseModel):
    rating: Literal["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    conviction: int = Field(ge=0, le=100)
    thesis: str
    key_risks: list[str]
    what_would_change_my_mind: list[str]
    citations: list[Citation]


# ── Prompts ───────────────────────────────────────────────────

SYSTEM = """You sit on an investment research committee reviewing one NSE-listed stock for a \
long-term, delivery-only investor in India. Your output is advice for the investor to read. \
You never place, size or time orders.

Ground rules:
- Use only the snapshot below. If something you would need is missing, say so plainly. Never \
estimate a figure or recall one from memory.
- Every number you write must also appear in `citations` with its snapshot key, the value as \
given and its unit. "pct" values are percent points: 12.5 means 12.5%.
- `unavailable` lists data that could not be loaded. Treat it as unknown, not as good or bad news.
- Give a clear view with its reasons rather than hedging, and say what the data cannot tell you.

Snapshot (schema v{version}):
{snapshot}"""

ROLES = {
    "fundamentals": "You are the fundamentals analyst. Judge business quality and valuation from "
    "the fundamentals section: profitability, growth, leverage, and what the price-to-earnings "
    "and price-to-book multiples imply. If the section is unavailable, keep confidence low and "
    "say why.",
    "technical": "You are the technical analyst. Judge trend and momentum from the price "
    "section: returns over each window, the 50-day versus 200-day average, RSI, distance from "
    "the 52-week high and drawdown.",
    "risk": "You are the risk analyst. Judge what could go wrong for this investor: volatility, "
    "worst drawdown, the position's weight in the portfolio and its unrealised gain or loss. A "
    "large weight in a volatile stock is a risk even when the outlook is good.",
    "graham": "Apply Benjamin Graham's value-investing principles: a margin of safety, moderate "
    "price-to-earnings and price-to-book multiples, low leverage and consistent profitability. "
    "Say whether this stock passes those tests on the data given.",
    "buffett": "Apply Warren Buffett's principles: a durable business with high returns on "
    "equity, low debt and steady margins, bought at a sensible price. Say whether this looks "
    "like a business worth owning for many years on the data given.",
    "lynch": "Apply Peter Lynch's principles: earnings growth at a reasonable price (compare "
    "growth with the price-to-earnings multiple), a strong balance sheet, and whether the price "
    "trend confirms the story.",
}
DEBATE = {
    "bull": "You are the bull researcher. Using the analyst views below, make the strongest "
    "honest case for owning this stock now.",
    "bear": "You are the bear researcher. Using the analyst views below, make the strongest "
    "honest case against owning this stock now.",
}
REBUT = " Answer the other side's latest argument directly and do not repeat your earlier points."
PM = (
    "You are the portfolio manager. Weigh the analyst views and the debate below, then rate the "
    "stock for this investor: Buy or Overweight to add, Hold to keep the position as it is, "
    "Underweight or Sell to reduce. Conviction runs from 0 to 100. List the main risks and the "
    "specific observations that would change your mind."
)


def prompt_hashes() -> dict[str, str]:
    """sha256 of each stage's template, so a run records exactly which prompts produced it."""
    templates = {**ROLES, **DEBATE, "rebut": REBUT, "portfolio_manager": PM}
    return {k: hashlib.sha256((SYSTEM + v).encode()).hexdigest() for k, v in templates.items()}


# ── Budget and cost ───────────────────────────────────────────


class Budget:
    """Reserve the worst case before a call, settle the actual cost after it."""

    def __init__(self, cap_usd: float):
        self.cap, self.spent, self.reserved = cap_usd, 0.0, 0.0
        self._lock = threading.Lock()

    def reserve(self, worst: float) -> bool:
        with self._lock:
            if self.spent + self.reserved + worst > self.cap:
                return False
            self.reserved += worst
            return True

    def settle(self, worst: float, actual: float) -> None:
        with self._lock:
            self.reserved -= worst
            self.spent += actual


def _price_cost(p: tuple, u) -> float:
    return (
        (u.input_tokens or 0) * p[0]
        + (u.output_tokens or 0) * p[1]
        + (getattr(u, "cache_read_input_tokens", 0) or 0) * p[2]
        + (getattr(u, "cache_creation_input_tokens", 0) or 0) * p[3]
    ) / 1e6


def call_cost(usage, model: str) -> float:
    """Actual USD cost. `usage.iterations` lists every attempt (a declined one plus its fallback)."""
    iters = getattr(usage, "iterations", None) or []
    if iters:
        return sum(_price_cost(PRICES.get(getattr(i, "model", model), MAX_PRICE), i) for i in iters)
    return _price_cost(PRICES.get(model, MAX_PRICE), usage)


def worst_case(model: str, prompt_chars: int, max_tokens: int) -> float:
    """Upper bound for one call: every input token written to cache, every output token used,
    and a declined attempt followed by a fallback at the most expensive listed price."""
    # ponytail: chars/2.5 over-estimates tokens on purpose; switch to count_tokens if too loose
    tokens_in = prompt_chars / 2.5

    def one(p: tuple) -> float:
        return (tokens_in * p[3] + max_tokens * p[1]) / 1e6

    return one(PRICES.get(model, MAX_PRICE)) + one(MAX_PRICE)


# ── Pipeline ──────────────────────────────────────────────────


@dataclass
class CommitteeConfig:
    quick_model: str
    deep_model: str
    budget_usd: float
    deadline: float  # time.monotonic() value after which no new call starts


class _Stop(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _read(resp, schema: type[BaseModel]) -> dict:
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        return {"status": "declined", "category": getattr(details, "category", None)}
    if resp.stop_reason == "max_tokens":
        return {"status": "truncated"}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return {"status": "ok", "output": schema.model_validate_json(text).model_dump()}
    except ValidationError as exc:
        return {"status": "invalid_output", "reason": exc.errors()[0]["msg"]}


def run_committee(
    snapshot: dict,
    client: anthropic.Anthropic,
    cfg: CommitteeConfig,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    system_text = SYSTEM.format(version=SNAPSHOT_SCHEMA_VERSION, snapshot=to_prompt_json(snapshot))
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    models = {"quick": cfg.quick_model, "deep": cfg.deep_model}
    budget = Budget(cfg.budget_usd)
    lock = threading.Lock()
    out: dict = {
        "status": "running",
        "stages": {},
        "decision": None,
        "calls": [],
        "error": None,
        "models": {
            "analysts": cfg.quick_model,
            "debate": cfg.quick_model,
            "portfolio_manager": cfg.deep_model,
        },
        "prompt_hashes": prompt_hashes(),
    }

    def progress() -> None:
        out["cost_usd"] = round(budget.spent, 4)
        if on_progress:
            on_progress(out)

    def request(stage: str, model: str, effort: str, max_tokens: int, user: str, schema=None):
        params = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if schema is not None:  # max_tokens=0 warm-up rejects a format and needs no fallback
            params["output_config"]["format"] = {
                "type": "json_schema",
                "schema": anthropic.transform_schema(schema),
            }
            params.update(betas=[FALLBACK_BETA], fallbacks="default")
        worst = worst_case(model, len(system_text) + len(user), max_tokens)
        if time.monotonic() > cfg.deadline:
            raise _Stop("abandoned", "The run passed its time limit")
        if not budget.reserve(worst):
            return None
        started = time.monotonic()
        try:
            resp = client.beta.messages.create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            budget.settle(worst, 0.0)
            raise _Stop("failed", "The Anthropic API key was rejected") from exc
        except anthropic.BadRequestError as exc:
            budget.settle(worst, 0.0)
            raise _Stop("failed", f"Request rejected: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            budget.settle(worst, 0.0)
            raise _Stop("failed", "Rate limited by the API after retries") from exc
        except anthropic.APIStatusError as exc:
            budget.settle(worst, 0.0)
            raise _Stop("failed", f"API error {exc.status_code} after retries") from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            budget.settle(worst, 0.0)
            raise _Stop("failed", "Network error or timeout reaching the API") from exc
        cost = call_cost(resp.usage, resp.model)
        budget.settle(worst, cost)
        u = resp.usage
        with lock:
            out["calls"].append(
                {
                    "stage": stage,
                    "requested_model": model,
                    "model": resp.model,
                    "effort": effort,
                    "request_id": getattr(resp, "_request_id", None),
                    "stop_reason": resp.stop_reason,
                    "fallback": any(
                        getattr(i, "type", "") == "fallback_message"
                        for i in (getattr(u, "iterations", None) or [])
                    ),
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                    "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0)
                    or 0,
                    "ms": round((time.monotonic() - started) * 1000),
                    "cost_usd": round(cost, 6),
                }
            )
        return resp

    def stage_call(stage: str, kind: str, user: str, schema: type[BaseModel]) -> dict:
        slot, effort, max_tokens = SETTINGS[kind]
        resp = request(stage, models[slot], effort, max_tokens, user, schema)
        return {"status": "skipped", "reason": "budget"} if resp is None else _read(resp, schema)

    def context(views: dict, transcript: list) -> str:
        text = "\n\nAnalyst views:\n" + json.dumps(views, sort_keys=True)
        if transcript:
            text += "\n\nDebate so far:\n" + json.dumps(transcript, sort_keys=True)
        return text

    try:
        # 1. Warm the shared prefix so the parallel analysts read it instead of all writing it.
        #    Whether later stages hit the cache is measured (cache_read_input_tokens), not assumed.
        try:
            if request("warmup", cfg.quick_model, "low", 0, "warmup") is None:
                raise _Stop("budget_exceeded", "The budget is too small for a single call")
        except _Stop as stop:
            if stop.status != "failed" or "API key" in str(stop):
                raise  # budget, time limit and a bad key stop the run; other warm-up errors don't
            out["calls"].append({"stage": "warmup", "error": str(stop)})  # not fatal

        # 2. Analysts and personas, in parallel
        stop: _Stop | None = None
        with ThreadPoolExecutor(MAX_PARALLEL) as pool:
            futures = {
                pool.submit(stage_call, name, "analyst", ROLES[name], AnalystView): name
                for name in ROLES
            }
            for fut in as_completed(futures):
                try:
                    out["stages"][futures[fut]] = fut.result()
                except _Stop as exc:
                    stop = stop or exc
                    for f in futures:
                        f.cancel()
                progress()
        if stop:
            raise stop
        views = {n: s["output"] for n, s in out["stages"].items() if s["status"] == "ok"}
        if len(views) < 2:
            statuses = {s["status"] for s in out["stages"].values()}
            if "skipped" in statuses:
                raise _Stop("budget_exceeded", "The budget ran out during the analyst stage")
            if "declined" in statuses:
                raise _Stop("declined", "The model declined to analyse this stock")
            raise _Stop("failed", "Fewer than two analysts returned a usable view")

        # 3. Bull/bear debate
        transcript: list[dict] = []
        for rnd in range(1, DEBATE_ROUNDS + 1):
            for side in ("bull", "bear"):
                prompt = DEBATE[side] + (REBUT if transcript else "") + context(views, transcript)
                res = stage_call(f"{side}_{rnd}", "debate", prompt, DebateTurn)
                out["stages"][f"{side}_{rnd}"] = res
                progress()
                if res["status"] == "skipped":
                    raise _Stop("budget_exceeded", "The budget ran out during the debate")
                if res["status"] == "ok":
                    transcript.append({"side": side, "round": rnd, **res["output"]})

        # 4. Portfolio manager
        res = stage_call(
            "portfolio_manager", "pm", PM + context(views, transcript), CommitteeDecision
        )
        out["stages"]["portfolio_manager"] = res
        if res["status"] == "skipped":
            raise _Stop("budget_exceeded", "The budget ran out before the final decision")
        if res["status"] == "declined":
            raise _Stop("declined", "The model declined to give a final rating")
        if res["status"] != "ok":
            raise _Stop("failed", f"The final decision was not usable ({res['status']})")
        out["decision"] = res["output"]
        out["status"] = "completed"
    except _Stop as stop:
        out["status"], out["error"] = stop.status, str(stop)

    out["grounding"] = grounding(out["stages"], allowlist(snapshot))
    out["cost_usd"] = round(budget.spent, 4)
    return out


def grounding(stages: dict, allowed: dict[str, dict]) -> dict:
    checks = [
        {"stage": name, **check_citation(c, allowed)}
        for name, s in stages.items()
        if s.get("status") == "ok"
        for c in s["output"].get("citations", [])
    ]
    passed = sum(c["ok"] for c in checks)
    return {
        "checked": len(checks),
        "passed": passed,
        "rate": round(passed / len(checks), 3) if checks else None,
        "failures": [c for c in checks if not c["ok"]],
    }
