"""A language-model fallback for pulling fixture fields out of requirement prose.

Why a FALLBACK and not the main path
------------------------------------
requirement_matcher.py's regexes are excellent on the documents this studio writes. Measured
on a real 8-page, 52-fixture Studio requirement PDF (12.08.2026 Studio-SampleClient): 30 fixtures gave
all 9 fields, 17 gave 8, 4 gave 7, and exactly ONE fell below that. There is nothing to fix
there, and replacing it would risk a proven 8.4-of-9 average for a network call.

What the regexes cannot do is read a document written by somebody else. The same fixture
phrased as a sentence, or as `7W | 3000K | 24deg | Ra90`, or with different labels, drops
from 9 fields to 3-5 - they are matching one firm's house style, not the language. That is
the gap this closes, and only that gap.

Running the model only on blocks the regexes struggled with also makes the cost trivial: on
that same document it is ~600 input tokens rather than ~23,000 for the whole PDF - about 3%
- which keeps a free API tier comfortably sufficient and leaves the tool working on a day the
quota is gone.

Rules this holds to
-------------------
  It only ADDS. A field the regexes already found is never overwritten. Today's behaviour on
  today's documents is therefore unchanged by construction, and the worst an outage can do is
  return exactly what the regexes returned.

  It never invents. The prompt forbids inference, and anything the model returns that is not
  a known canonical field, or is empty, is dropped here rather than trusted. A field nobody
  stated must stay absent: requirement_matcher scores only fields present on BOTH sides, so
  an invented value does not merely add noise, it changes the ranking.

  It never breaks the page. Any failure - no key, disabled, timeout, malformed JSON, bad
  status - returns the regex fields unchanged. This is a tool people use on a deadline.

Configuration (environment, never committed):
    LLM_EXTRACT_ENABLED=1          off unless explicitly turned on
    OPENROUTER_API_KEY=...         an OpenRouter key - free models only (see below); or
    GROQ_API_KEY=...               a Groq key (used when there is no OpenRouter key)
    LLM_EXTRACT_MODEL=...          optional; one model, or for OpenRouter a comma-separated list
                                   of ":free" models tried in order
    LLM_DAILY_REQUESTS=...         optional; requests this server may make per day (UTC), all
                                   workers together (counted in data/llm_usage/) - default 950
                                   on OpenRouter (its free allowance is 1,000 a day once $10 of
                                   credit has been bought, 50 before that)
    LLM_ALLOW_TRAINING=1           optional; lets OpenRouter use providers that may train on
                                   what is sent. Off by default: a client's BOQ is confidential
    LLM_EXTRACT_MIN_FIELDS=7       call the model only for blocks below this many fields

OpenRouter (added 28 Oct 2026, branch FreeLLM) gives one key to many providers' free models,
with a far larger daily allowance than Groq's free tier. Three rules hold for it here: only
models whose id ends in ":free" are ever sent, because OpenRouter bills a paid model at its rate
if a fallback reaches one; every request asks for providers that do not collect data
(`"data_collection": "deny"`), so a client's document is never sent to one that may train on
it; and the list of models is a fallback chain, so a free model that is busy or withdrawn is
passed over rather than failing the request.
"""
import datetime
import json
import os
import re
import time

# Groq and OpenRouter both speak the OpenAI chat-completions shape, so this needs no SDK - one
# POST with httpx, which the project already depends on.
API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# OpenRouter's free models, tried in this order. A model id is a moving target: free models come
# and go, so this is a fallback chain, and LLM_EXTRACT_MODEL replaces it. Each was tried with the
# real key on 29 Oct 2026, and to be here a model must: take JSON output, have a provider that does
# not collect data (nvidia/nemotron-3.5-super-vl:free has only one, and it may train - refused),
# and let its "thinking" be switched off (recursive/flint-v1.0:free cannot, and OpenRouter then
# refuses the whole request, whatever comes after it in the chain). gemma and xingchen were busy
# upstream that day (a shared free pool), which is what the chain is for.
OPENROUTER_FREE_MODELS = (
    "dots-studio/dots-3-note-preview:free",
    "google/gemma-4-31b-it:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
)
# OpenRouter refuses a request whose fallback list names more than 3 models (HTTP 400, "'models'
# array must have 3 items or fewer" - found on the first request with a real key, 29 Oct 2026), so
# only the first 3 of the list are ever sent.
OPENROUTER_MAX_MODELS = 3
# Measured on the free tier: the model's own compute is ~0.15s and a normal call returns in
# 0.6-0.7s, but roughly one call in three sits in a queue for ~22 SECONDS. That is the tier,
# not the model - it happens identically on every model tested.
#
# So the wait is capped twice. Per call, a few seconds is plenty for something that computes
# in 0.15s; beyond that we are queueing, and the regex fields we already hold are worth more
# than a better answer that arrives after the user has given up. Per REQUEST, a whole-run
# budget stops a document with many thin blocks from adding queue after queue - the first few
# get the model, the rest fall back, and nobody watches a spinner for a minute.
# Chosen by testing every general-purpose model this account can actually reach, on this
# exact task: qwen answered in 747ms where openai/gpt-oss-120b took 22.7 SECONDS for the
# same 7-of-9 fields and the same zero invented ones, and openai/gpt-oss-20b would not
# produce usable JSON at all. Groq has retired the llama-3.x chat models this first named,
# which is worth knowing generally: a model id is a moving target, so the failure when one
# disappears has to be survivable - it is, see extract_fields().
DEFAULT_MODEL = "qwen/qwen3.8-27b"
# Latency here is bimodal, not spread out: a call either returns in ~0.7s or sits in a
# queue for ~22s. So a SHORT timeout loses nothing - anything not back in 4s was never
# going to be quick - and wastes four seconds instead of six proving it.
TIMEOUT_SECONDS = 4
RUN_BUDGET_SECONDS = 20

# The only keys that may come back. Anything else the model volunteers is discarded - these
# are requirement_matcher's own canonical fields, and a value under an unknown name cannot be
# scored against a specsheet anyway.
ALLOWED_FIELDS = (
    "wattage", "cct", "beam_angle", "cri", "ip_rating", "control", "finish", "material",
    "mounting", "warranty", "ugr", "lumen_output", "cutout_dimensions", "dimensions",
    "accessories", "input_voltage", "ik_rating",
)

SYSTEM_PROMPT = (
    "You extract lighting fixture specifications from an architect's requirement text.\n"
    "Return ONLY a JSON object mapping field names to values. Allowed field names:\n"
    + ", ".join(ALLOWED_FIELDS) + ".\n"
    "\n"
    "Rules:\n"
    "1. Include a field ONLY if the text states it explicitly. Never infer, never estimate, "
    "never fill a typical value. Omit anything not stated.\n"
    "2. Write every value in DIGITS, never words, even where the text spells it out:\n"
    "   wattage '7W' (NOT 'seven watts'), cct '3000K', warranty '5 years'.\n"
    "   beam_angle, cri and ip_rating are bare numbers: '24', '90', '54'.\n"
    "   control is one of: DALI, Phase Cut, 0-10V, Zigbee, Casambi, DMX, On-Off.\n"
    "3. If the text gives a range, return the range as written ('3000-3500K'). "
    "Do not pick one end of it.\n"
    "4. If the text states nothing you recognise, return {}.\n"
    "Return the JSON object and nothing else."
)


# Why the most recent call failed, for reporting only. A spent free-tier quota is by far the
# commonest reason this feature does nothing, and reporting it as "found nothing" makes a
# working integration look broken.
LAST_ERROR = ""


def last_error() -> str:
    return LAST_ERROR


def _safe_json(response):
    """The response body as a dict, or {} - an error body is not guaranteed to be JSON."""
    try:
        return response.json()
    except Exception:
        return {}


def _note_failure(status, exc=None, body=None, message=None):
    """Records the reason in words a user can act on, not a stack trace.

    For a 429 the provider's own message names which of its several limits was hit and when
    it clears; those numbers are worth far more than the fact of a refusal, so they are read
    out of the body rather than guessed at.
    """
    global LAST_ERROR
    text = message or ""
    if not text and isinstance(body, dict) and isinstance(body.get("error"), dict):
        text = body["error"].get("message") or ""
    # OpenRouter's own message for a busy free model is only "Provider returned error"; what
    # happened is in the error's metadata.
    raw = ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        raw = str((body["error"].get("metadata") or {}).get("raw") or "") if isinstance(
            body["error"].get("metadata"), dict) else ""
    low = (text + " " + raw).lower()
    if "rate-limited upstream" in low:
        LAST_ERROR = ("the free AI models are all busy right now (OpenRouter's shared free pool) - "
                      "try again in a minute")
        return
    # OpenRouter's own refusals, each with what to do about it.
    if "free-models-per-day" in low or "free model" in low and "per day" in low:
        LAST_ERROR = ("today's free AI allowance on OpenRouter is used up (50 requests a day; 1,000 "
                      "once $10 of credit has been bought on the account). It renews tomorrow")
        return
    if "data policy" in low or "no endpoints found" in low:
        LAST_ERROR = ("no free AI model is available right now that keeps the data private (the app "
                      "asks for providers that do not collect data) - try again later")
        return
    if status == 402:
        LAST_ERROR = "the AI provider asked for payment - the configured model is not a free one"
        return
    if status == 0 and exc is None and text:
        LAST_ERROR = "the AI provider refused the request: " + text[:200]
        return
    if status == 429:
        detail = ""
        try:
            message = ((body or {}).get("error") or {}).get("message") or ""
        except AttributeError:
            message = ""
        kind = re.search(r"on (tokens per day|tokens per minute|requests per day|"
                         r"requests per minute)", message, re.I)
        used = re.search(r"Limit\s+([\d,]+),\s*Used\s+([\d,]+)", message, re.I)
        again = re.search(r"try again in ([\dhms.]+)", message, re.I)
        if kind:
            detail = f" ({kind.group(1)}"
            if used:
                # [\d,]+ also swallows the comma that SEPARATES the numbers in the provider's
                # message ("Used 199199, Requested ..."), so each is trimmed and then
                # re-formatted - 199,199 of 200,000 reads as a budget, 199199 reads as noise.
                def _n(raw):
                    digits = raw.strip().strip(",").replace(",", "")
                    return f"{int(digits):,}" if digits.isdigit() else raw
                detail += f" - {_n(used.group(2))} of {_n(used.group(1))} used"
            detail += ")"
        when = f" Try again in {again.group(1).rstrip('.')}." if again else ""
        LAST_ERROR = (f"the AI provider's usage limit was reached{detail}.{when} "
                      f"The daily allowance on a free plan is modest - a large document can "
                      f"use several thousand tokens.")
    elif status == 401:
        LAST_ERROR = "the AI provider rejected the API key"
    elif status == 404:
        LAST_ERROR = "the configured AI model no longer exists on this account"
    elif status:
        # The provider's own words say what was wrong with the request; "HTTP 400" alone does not.
        LAST_ERROR = f"the AI provider returned HTTP {status}" + (f" ({text[:200]})" if text else "")
    elif exc is not None:
        LAST_ERROR = f"could not reach the AI provider ({type(exc).__name__})"


# What the provider last told us about the token budget, and when it resets. Tokens per
# minute is the limit that binds this workload, not requests per day.
_RATE = {"remaining": None, "reset_at": 0.0}
# Never wait longer than this for a budget reset. A document upload can absorb a pause; it
# cannot absorb an unbounded one, and the rule-based reading is always there to fall back on.
MAX_RATE_WAIT_SECONDS = 45


def _parse_reset(value):
    """Groq writes resets as '23m2.4s', '134ms', '7.5s'. Returns seconds."""
    import re as _re
    if not value:
        return 0.0
    total, found = 0.0, False
    for amount, unit in _re.findall(r"([\d.]+)\s*(ms|s|m|h)", str(value)):
        found = True
        amount = float(amount)
        total += {"ms": amount / 1000, "s": amount, "m": amount * 60,
                  "h": amount * 3600}[unit]
    if found:
        return total
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _note_rate(headers):
    """Remembers what the provider just said about the remaining token budget."""
    import time as _time
    try:
        remaining = headers.get("x-ratelimit-remaining-tokens")
        reset = headers.get("x-ratelimit-reset-tokens")
        if remaining is not None:
            _RATE["remaining"] = int(float(remaining))
        if reset is not None:
            _RATE["reset_at"] = _time.time() + _parse_reset(reset)
    except (TypeError, ValueError):
        pass


def _await_budget(estimated_tokens):
    """Waits, if the next call plainly will not fit in what is left this minute.

    Only ever waits for a reset the provider itself has told us about, and never longer than
    MAX_RATE_WAIT_SECONDS - past that it is better to fall back to the rule-based reading than
    to hold a page open indefinitely.
    """
    import time as _time
    remaining = _RATE["remaining"]
    if remaining is None or remaining >= estimated_tokens:
        return
    wait = min(MAX_RATE_WAIT_SECONDS, max(0.0, _RATE["reset_at"] - _time.time()) + 0.5)
    if wait > 0:
        _time.sleep(wait)
        # After a reset the budget is whole again; assume so rather than call to find out.
        _RATE["remaining"] = None


def _estimate_tokens(text) -> int:
    """Roughly four characters per token, plus the prompt's own overhead. Deliberately an
    over-estimate: pausing needlessly costs a second, being refused costs the whole call."""
    return int(len(text or "") / 3.2) + 600


def provider() -> str:
    """Which service the model is reached through: OpenRouter when its key is set, else Groq."""
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        return "openrouter"
    if os.environ.get("GROQ_API_KEY", "").strip():
        return "groq"
    return ""


def is_enabled() -> bool:
    """Off unless switched on AND a key is present - so a machine without the key behaves
    exactly like one with the feature turned off, rather than erroring per request."""
    return bool(os.environ.get("LLM_EXTRACT_ENABLED", "").strip() and provider())


def models() -> list:
    """The models a request may use, in order. On OpenRouter only free ones: a fallback that
    reached a paid model would be billed at its rate, and this feature is meant to cost nothing."""
    configured = [m.strip() for m in os.environ.get("LLM_EXTRACT_MODEL", "").split(",") if m.strip()]
    if provider() == "openrouter":
        free = [m for m in (configured or list(OPENROUTER_FREE_MODELS)) if m.endswith(":free")]
        return free[:OPENROUTER_MAX_MODELS]
    return configured[:1] or [DEFAULT_MODEL]


# Requests made today are counted under data/, one byte per request in a file named for the UTC
# date (the providers' daily allowances renew at UTC midnight). A file, not a variable: the server
# runs two worker processes and restarts on every deploy, and a count kept in memory would let each
# worker, and each restart, use the whole allowance again. The cap sits below the provider's own
# allowance so the last requests of a busy day are refused here, with a reason, not by the provider.
LAST_MODEL = ""


def daily_cap() -> int:
    """How many requests this server makes in a day: LLM_DAILY_REQUESTS when set; else what the
    key's own plan allows (plan()), less a margin for anyone else using the same key; else 950."""
    try:
        return int(os.environ.get("LLM_DAILY_REQUESTS", ""))
    except ValueError:
        pass
    allowed = plan().get("per_day")
    if allowed:
        return allowed - max(2, allowed // 20)        # 48 of 50, 950 of 1,000
    return 950 if provider() == "openrouter" else 1000


# OpenRouter's free models allow 50 requests a day, or 1,000 on an account that has bought $10 of
# credit at some point. Which one this key has is asked of OpenRouter itself (GET /credits: an
# account question, not a model request - it uses none of the allowance), at most once an hour,
# so the cap follows the key when credit is bought or the key is swapped for another account's.
_PLAN = {"checked": 0.0, "per_day": None, "note": ""}
PLAN_SECONDS = 3600


def plan() -> dict:
    """{"per_day": 50 | 1000 | None, "note": what the page can say about it}."""
    if provider() != "openrouter":
        return {"per_day": None, "note": ""}
    if time.time() - _PLAN["checked"] < PLAN_SECONDS:
        return dict(_PLAN)
    _PLAN["checked"] = time.time()
    try:
        import httpx
        # The same host the requests go to, so a stand-in server (LLM_API_URL) answers this too.
        base = (os.environ.get("LLM_API_URL", "").strip() or OPENROUTER_URL).rsplit("/chat/completions", 1)[0]
        response = httpx.get(base + "/credits", timeout=5,
                             headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"].strip()})
        bought = float(((response.json() or {}).get("data") or {}).get("total_credits") or 0)
        if response.status_code == 200:
            per_day = 1000 if bought >= 10 else 50
            _PLAN.update(per_day=per_day, note=(
                "OpenRouter free plan: 1,000 requests a day (credit bought on the account)" if per_day == 1000 else
                "OpenRouter free plan: 50 requests a day (1,000 once $10 of credit is bought)"))
    except Exception:
        _PLAN["checked"] = time.time() - PLAN_SECONDS + 300   # unknown: ask again in 5 minutes
    return dict(_PLAN)


def _usage_file():
    from .database import DATA_DIR
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    return DATA_DIR / "llm_usage" / (today + ".txt")


def requests_today() -> int:
    try:
        return _usage_file().stat().st_size
    except OSError:
        return 0


def _take_request() -> bool:
    global LAST_ERROR
    if requests_today() >= daily_cap():
        LAST_ERROR = ("today's AI allowance on this server is used up (%d requests) - the app's own "
                      "reading carries on, and the allowance renews tomorrow" % daily_cap())
        return False
    try:
        path = _usage_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Appending, so two workers counting at once each add their byte rather than overwrite.
        with open(path, "ab") as f:
            f.write(b".")
    except OSError:
        pass   # an uncounted request beats no request; the provider's own limit still holds
    return True


def _request_timeout(seconds) -> float:
    """OpenRouter's free models queue for longer than Groq's did; a short timeout there would give
    up on answers that were coming."""
    return max(seconds, 25) if provider() == "openrouter" else seconds


def chat(system: str, user: str, max_tokens: int, timeout: float):
    """-> the model's reply (text it was asked to make JSON), or None for any failure at all, with
    the reason in LAST_ERROR. Raises nothing. The one place this module talks to a provider."""
    global LAST_MODEL, LAST_ERROR
    if not is_enabled():
        return None
    chain = models()
    if not chain:
        _note_failure(0, message="no free model is configured (LLM_EXTRACT_MODEL names only paid ones)")
        return None
    if not _take_request():
        return None
    import httpx
    payload = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        # Zero temperature and a JSON response format: extraction has one right answer, and
        # reading the same document again should not produce a different BOQ.
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    if provider() == "openrouter":
        url, key = OPENROUTER_URL, os.environ["OPENROUTER_API_KEY"].strip()
        payload["models"] = chain
        payload["provider"] = {"data_collection": "allow" if os.environ.get("LLM_ALLOW_TRAINING", "").strip()
                               else "deny"}
        # Thinking off. Reading a 6-line quotation, the first free model spent 5,793 of its 8,000
        # tokens thinking, ran out halfway through the answer and took 86 s; with thinking off the
        # same answer came back whole in 10 s. Copying values out of a BOQ needs no deliberation.
        payload["reasoning"] = {"enabled": False}
        extra = {"HTTP-Referer": os.environ.get("LLM_APP_URL", "https://quotes.example.com"),
                 "X-Title": "Quotation Tool"}
    else:
        url, key = API_URL, os.environ["GROQ_API_KEY"].strip()
        payload["model"] = chain[0]
        extra = {}
    # LLM_API_URL points the same request somewhere else - a stand-in server for tests, or a
    # self-hosted model that speaks the same shape.
    url = os.environ.get("LLM_API_URL", "").strip() or url
    _await_budget(_estimate_tokens(system + user))
    try:
        response = httpx.post(url, json=payload, timeout=_request_timeout(timeout),
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", **extra})
        _note_rate(response.headers)
        body = _safe_json(response)
        if response.status_code != 200 or body.get("error"):
            _note_failure(response.status_code if response.status_code != 200 else 0, body=body,
                          message=((body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None))
            return None
        LAST_MODEL = str(body.get("model") or "")
        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if choice.get("finish_reason") == "length":
            # Stopped at max_tokens: this is the start of an answer, and anything read from it
            # would be missing its end without saying so.
            LAST_ERROR = "the AI model's answer was cut off before it finished"
            return None
        if not content:
            LAST_ERROR = "the AI model sent back an empty answer"
            return None
        return content
    except Exception as exc:
        _note_failure(0, exc)
        return None


def _min_fields() -> int:
    try:
        return int(os.environ.get("LLM_EXTRACT_MIN_FIELDS", "7"))
    except ValueError:
        return 7


def _clean(value) -> str:
    """Values arrive as free JSON. Anything that is not a short scalar is not a field value."""
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split()).strip()
    if len(value) > 120:
        return ""
    # A model that has been told to omit unknowns sometimes says so instead.
    if value.lower() in {"", "none", "n/a", "na", "not stated", "not specified", "unknown",
                         "null", "-", "not mentioned"}:
        return ""
    return value


def _parse_json_object(content: str):
    """The JSON object out of a chat response, tolerant of a model wrapping it in prose or a
    fenced block. Shared by the single and batch parsers - they differ only in what they
    expect to find inside it."""
    if not content:
        return None
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = text.find("{")
        if brace == -1:
            return None
        text = text[brace:]
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _parse_response(content: str) -> dict:
    """The JSON object out of a chat response, tolerant of a model wrapping it in prose or a
    fenced block, and strict about what it accepts afterwards."""
    if not content:
        return {}
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = text.find("{")
        if brace == -1:
            return {}
        text = text[brace:]
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        key = str(key).strip().lower().replace(" ", "_")
        if key in ALLOWED_FIELDS:
            cleaned = _clean(value)
            if cleaned:
                out[key] = cleaned
    return out


class RunBudget:
    """How much total wall-clock a single Match request may spend waiting on the model.

    Without this, a document with twenty thin blocks could queue twenty times over. With it,
    the early blocks get the model and the rest quietly use the regexes - which is the right
    trade, because every block already has a usable answer before the model is asked at all.
    """

    def __init__(self, seconds=None):
        self.remaining = RUN_BUDGET_SECONDS if seconds is None else seconds
        self.degraded = False

    def take(self, spent, succeeded):
        self.remaining -= spent
        # One timeout is enough to stop asking. The queue that swallowed this call is the
        # same queue the next one would join, so trying again spends the budget four seconds
        # at a time to learn what is already known. The regexes have answered every block
        # already; a degraded run is slower to help, never worse.
        if not succeeded and spent >= TIMEOUT_SECONDS:
            self.degraded = True

    def exhausted(self):
        return self.degraded or self.remaining <= 0


def extract_fields(text: str) -> dict:
    """-> {field: value} the model found, or {} for any failure at all.

    Raises nothing. A caller must be able to treat this as "extra fields, if any".
    """
    if not is_enabled() or not (text or "").strip():
        return {}
    # Any failure - a timeout, a DNS failure, a quota refusal, a malformed body - means the same
    # thing to the caller: carry on with what the regexes found.
    content = chat(SYSTEM_PROMPT, text[:12000], 800, TIMEOUT_SECONDS)
    return _parse_response(content) if content else {}


def augment(fields: dict, text: str, counted_fields=None, budget=None) -> tuple:
    """-> (fields, used_llm). Fills only what the regexes missed.

    `counted_fields` is which keys count towards "did the regexes do well enough" - pass the
    same list the caller judges completeness by, so the threshold means what it appears to.
    """
    if not is_enabled():
        return fields, False
    keys = counted_fields or ALLOWED_FIELDS
    found = sum(1 for k in keys if (fields or {}).get(k))
    if found >= _min_fields():
        return fields, False
    if budget is not None and budget.exhausted():
        return fields, False
    import time
    started = time.monotonic()
    extra = extract_fields(text)
    if budget is not None:
        budget.take(time.monotonic() - started, bool(extra))
    if not extra:
        return fields, False
    merged = dict(fields or {})
    added = False
    for key, value in extra.items():
        # Add-only. Overwriting would put a proven 8.4-of-9 extraction at the mercy of a
        # network call, and the regexes are the more precise of the two on a document
        # written in the house style.
        if not merged.get(key):
            merged[key] = value
            added = True
    return merged, added


# --- one request per document, not per fixture ---------------------------------------------
#
# The single-block path above is the right shape on a responsive endpoint and the wrong one
# here. On this tier the wait attaches to the REQUEST, not to the work: a call either returns
# in ~0.7s or sits in a queue for ~22s. Eleven thin fixtures asked one at a time therefore
# offered to pay that queue eleven times, the run budget refused after the first - correctly -
# and the model ended up helping with nothing at all. Measured: 0 of 11 fixtures improved.
#
# Asked together they pay it once. Same tokens, same add-only rules, one wait.
BATCH_SYSTEM_PROMPT = (
    "You extract lighting fixture specifications from an architect's requirement document.\n"
    "The user gives numbered fixtures. Return ONLY a JSON object whose keys are those same "
    "numbers as strings, and whose values are objects of field name to value.\n"
    "Allowed field names: " + ", ".join(ALLOWED_FIELDS) + ".\n"
    '\nExample shape: {"1": {"wattage": "7W"}, "2": {"cct": "3000K"}}\n'
    "\nRules:\n"
    "1. Include a field ONLY if that fixture's own text states it explicitly. Never infer, "
    "never estimate, and never carry a value from one fixture to another. Omit what is not "
    "stated.\n"
    "2. Write every value in DIGITS, never words: wattage '7W' (NOT 'seven watts'), "
    "cct '3000K', warranty '5 years'. beam_angle, cri and ip_rating are bare numbers: "
    "'24', '90', '54'. control is one of: DALI, Phase Cut, 0-10V, Zigbee, Casambi, DMX, "
    "On-Off.\n"
    "3. If the text gives a range, return it as written ('3000-3500K'), never one end of it.\n"
    "4. Include an entry for every fixture number given; use {} for one that states nothing.\n"
    "Return the JSON object and nothing else."
)

# The wait is paid once per document, so it may be a longer one - the measured worst case on
# this tier is ~22s of queue, and a document upload is already something you wait for.
BATCH_TIMEOUT_SECONDS = 30
# Bounds one request. Past this the blocks go in several batches - still one request each,
# never one per fixture.
MAX_BATCH = 40


def extract_batch(texts) -> dict:
    """-> {index into `texts`: {field: value}}, or {} for any failure at all.

    Raises nothing, exactly like extract_fields(): a caller must be able to treat the result
    as "extra fields, if any".
    """
    if not is_enabled() or not texts:
        return {}
    numbered = "\n\n".join(f"### Fixture {i + 1}\n{t[:4000]}" for i, t in enumerate(texts))
    content = chat(BATCH_SYSTEM_PROMPT, numbered[:60000], 200 + 120 * len(texts), BATCH_TIMEOUT_SECONDS)
    if not content:
        return {}

    data = _parse_json_object(content)
    out = {}
    for key, value in (data or {}).items():
        try:
            idx = int(str(key).strip().lstrip("#").strip()) - 1
        except (ValueError, TypeError):
            continue
        if not (0 <= idx < len(texts)) or not isinstance(value, dict):
            continue
        cleaned = {}
        for field, raw in value.items():
            field = str(field).strip().lower().replace(" ", "_")
            if field in ALLOWED_FIELDS:
                good = _clean(raw)
                if good:
                    cleaned[field] = good
        if cleaned:
            out[idx] = cleaned
    return out


def augment_batch(items, counted_fields=None) -> tuple:
    """items: [(fields, text), ...] for a whole document -> (list of fields, fields added).

    Only blocks the regexes did poorly on are sent, and they go together. Add-only, exactly
    as the single-block path: a value the regexes found is never overwritten.
    """
    result = [dict(f or {}) for f, _ in items]
    if not is_enabled():
        return result, 0
    keys = counted_fields or ALLOWED_FIELDS
    thin = [i for i, (f, _) in enumerate(items)
            if sum(1 for k in keys if (f or {}).get(k)) < _min_fields()]
    if not thin:
        return result, 0

    added = 0
    for start in range(0, len(thin), MAX_BATCH):
        chunk = thin[start:start + MAX_BATCH]
        found = extract_batch([items[i][1] for i in chunk])
        if not found:
            # A failed batch means the tier is queueing or refusing, and the next batch would
            # meet the same queue. The rest of the document keeps the regex fields it has.
            break
        for position, i in enumerate(chunk):
            for field, value in (found.get(position) or {}).items():
                if not result[i].get(field):
                    result[i][field] = value
                    added += 1
    return result, added


# --- reading a whole document, not patching one block --------------------------------------
#
# Everything above helps a document the regexes ALREADY split correctly. That split is the
# weaker half. split_requirement_blocks() separates fixtures on blank lines, and where a real
# architect export has none it falls back to the literal phrase "Supply of" - which is one
# firm's wording. A document that says "Provide and install", or numbers its fixtures, or puts
# them in a table, collapses into one block or splits in the wrong places, and no amount of
# field extraction recovers from fixtures that were never separated.
#
# So when the user asks for it, the model does BOTH jobs: find where each fixture starts and
# ends, and pull the fields out of each. That is a different task from augment_batch(), which
# is why it is a different function rather than a flag on one.
#
# It is opt-in per upload, not a default. The regex path is faster, free, and better on the
# documents this studio writes; this is for the ones it is not.
DOCUMENT_SYSTEM_PROMPT = (
    "You read an architect's lighting requirement document and list the light fixtures it "
    "specifies.\n"
    "\n"
    "Return ONLY a JSON object of this shape:\n"
    '{"fixtures": [{"label": "Type A", "text": "the fixture\'s own requirement wording", '
    '"fields": {"wattage": "7W"}}]}\n'
    "\n"
    "Allowed field names: " + ", ".join(ALLOWED_FIELDS) + ".\n"
    "\n"
    "Rules:\n"
    "1. One entry per DISTINCT fixture the document specifies. Do not merge two fixtures "
    "into one entry, and do not split one fixture across two entries.\n"
    "2. `label` is the document's own reference for it if it has one (a type code like "
    "'A1', a name, a row reference). Use \"\" if it gives none. Never invent one.\n"
    "3. `text` is that fixture's own requirement wording, copied from the document, not "
    "summarised. It is shown to the user, so it must be the document's words.\n"
    "4. In `fields`, include a field ONLY if that fixture's text states it explicitly. Never "
    "infer, never estimate, never carry a value from one fixture to another, never fill a "
    "typical value. Omit what is not stated.\n"
    "5. Write values in DIGITS, never words: wattage '7W' (NOT 'seven watts'), cct '3000K', "
    "warranty '5 years'. beam_angle, cri and ip_rating are bare numbers: '24', '90', '54'. "
    "control is one of: DALI, Phase Cut, 0-10V, Zigbee, Casambi, DMX, On-Off.\n"
    "6. A range stays a range ('3000-3500K'). Never pick one end of it.\n"
    "7. Ignore page headers, column headings, totals, terms and anything that is not a "
    "fixture specification.\n"
    "Return the JSON object and nothing else."
)

# A document is read in slices so a long one still fits, and so one bad slice cannot lose the
# whole upload. Sized well inside the model's window, split on blank lines so a fixture is not
# cut in half.
DOCUMENT_CHUNK_CHARS = 12000
DOCUMENT_TIMEOUT_SECONDS = 60


# How much of the previous slice to repeat at the start of the next. Sized to comfortably
# contain one whole requirement (the real ones run 800-2600 characters), so a requirement cut
# by a boundary reappears complete in the following slice.
DOCUMENT_OVERLAP_CHARS = 3000


def _chunk_document(text: str, limit=DOCUMENT_CHUNK_CHARS, overlap=DOCUMENT_OVERLAP_CHARS):
    """Slices on paragraph boundaries, never mid-paragraph, with each slice repeating the tail
    of the one before it.

    Two separate problems, one fix each. Cutting mid-paragraph would read one requirement as
    two, so cuts land on blank lines. And a requirement sitting ACROSS a cut was seen whole by
    neither slice - the reason a real multi-page document came back with the wrong count - so
    each slice carries the previous slice's tail with it. The duplicates that creates are
    removed by _dedupe_fixtures, which keeps the longer copy of any pair.
    """
    text = text or ""
    # Paragraph pass first, so a document that HAS blank lines is cut only between them.
    pieces = []
    for piece in re.split(r"(\n\s*\n)", text):
        # A piece still over the limit has no usable paragraph breaks in it - a table export
        # from a PDF is typically one continuous run - so it is cut anyway, preferring a
        # sentence end, then any whitespace, then wherever it must.
        while len(piece) > limit:
            window = piece[:limit]
            cut = max(window.rfind(". "), window.rfind("; "))
            if cut < limit // 2:
                cut = window.rfind(" ")
            if cut < limit // 2:
                cut = limit
            pieces.append(piece[:cut])
            piece = piece[cut:]
        pieces.append(piece)

    chunks, current = [], ""
    for piece in pieces:
        if len(current) + len(piece) > limit and current.strip():
            chunks.append(current)
            # Carry the tail forward so a requirement sitting across the cut appears whole in
            # the next slice. Snapped back to a paragraph break where one exists, so the
            # repeated part does not itself begin mid-requirement.
            tail = current[-overlap:] if overlap else ""
            snap = tail.find("\n\n")
            current = (tail[snap + 2:] if snap != -1 else tail) + piece
        else:
            current += piece
    if current.strip():
        chunks.append(current)
    return chunks or ([text] if text else [])


def parse_document(text: str):
    """-> [{'label': str, 'text': str, 'fields': {...}}] for a whole requirement document.

    Empty list for any failure, so a caller can fall back to the regex path. Raises nothing.
    """
    if not is_enabled() or not (text or "").strip():
        return []
    fixtures = []
    for chunk in _chunk_document(text):
        content = chat(DOCUMENT_SYSTEM_PROMPT, chunk, 8000, DOCUMENT_TIMEOUT_SECONDS)
        if not content:
            continue              # this slice is lost, the rest of the document is not
        data = _parse_json_object(content) or {}
        for entry in (data.get("fixtures") or []):
            if not isinstance(entry, dict):
                continue
            raw_text = entry.get("text")
            raw_text = " ".join(str(raw_text).split()) if raw_text else ""
            if not raw_text:
                continue
            fields = {}
            for key, value in (entry.get("fields") or {}).items():
                key = str(key).strip().lower().replace(" ", "_")
                if key in ALLOWED_FIELDS:
                    good = _clean(value)
                    if good:
                        fields[key] = good
            label = " ".join(str(entry.get("label") or "").split())[:60]
            fixtures.append({"label": label, "text": raw_text, "fields": fields})
    return fixtures


# --- finding where each fixture starts -----------------------------------------------------
#
# The first attempt at this asked the model to return each fixture's full wording along with
# its fields. On a real 52-fixture document that answer does not fit: it hit the token ceiling
# and came back with 14 fixtures. Losing three quarters of a BOQ is far worse than any number
# of extra fields on the ones that survive.
#
# So the model is asked for something small instead - just where each fixture BEGINS, quoted
# from the document. The splitting is then done here, against the original text, and the
# fields come from augment_batch(), which is already tested. Three benefits fall out:
#
#   The answer is tiny (a dozen words per fixture), so nothing truncates.
#   The text shown to the user is the DOCUMENT'S OWN, byte for byte - not the model's copy of
#   it, which could quietly differ from what the architect wrote.
#   If the model returns nothing usable, the regex splitter is still there.
SPLIT_SYSTEM_PROMPT = (
    "You are given an architect's lighting requirement document. Find where each distinct "
    "light fixture's specification BEGINS.\n"
    "\n"
    "Return ONLY a JSON object of this shape:\n"
    '{"fixtures": [{"label": "RB1", "starts_with": "Supply of recessed trim fixed downlight"}]}\n'
    "\n"
    "Rules:\n"
    "1. One entry per DISTINCT fixture specified. Two fixtures that differ in any way - "
    "wattage, beam angle, finish, size - are different fixtures and get an entry each, even "
    "if their wording is otherwise nearly identical.\n"
    "2. `starts_with` MUST be the first 8 to 15 words of that fixture's specification, "
    "copied EXACTLY from the document, character for character. It is used to locate the "
    "fixture in the original text, so an approximation or a summary is useless.\n"
    "3. `label` is the document's own reference for the fixture (a type code like 'RB1', "
    "'Type A', a row reference) or \"\" if it gives none. Never invent one.\n"
    "4. Ignore page headers, column headings, totals, terms and anything that is not a "
    "fixture specification.\n"
    "5. List them in the order they appear in the document.\n"
    "Return the JSON object and nothing else."
)


def _locate(text, needle, from_index):
    """Where `needle` starts in `text` at or after from_index, tolerating the whitespace
    differences between a model's quote and a PDF's own line wrapping. -1 if not found."""
    if not needle:
        return -1
    direct = text.find(needle, from_index)
    if direct != -1:
        return direct
    # The model quotes with single spaces; the PDF may have newlines or double spaces inside
    # the same phrase. Match on words with flexible whitespace between them.
    words = [re.escape(w) for w in needle.split() if w]
    if len(words) < 3:
        return -1
    for take in (len(words), 8, 5, 3):
        if take > len(words):
            continue
        pattern = r"\s+".join(words[:take])
        m = re.search(pattern, text[from_index:], re.IGNORECASE)
        if m:
            return from_index + m.start()
    return -1


def split_document(text: str):
    """-> [(label, block_text)] using the model to find fixture boundaries, or [] on failure.

    The blocks are slices of the ORIGINAL text - the model says where to cut, never what the
    content is.
    """
    if not is_enabled() or not (text or "").strip():
        return []
    found = []
    for chunk in _chunk_document(text, DOCUMENT_CHUNK_CHARS):
        content = chat(SPLIT_SYSTEM_PROMPT, chunk, 6000, DOCUMENT_TIMEOUT_SECONDS)
        if not content:
            continue
        data = _parse_json_object(content) or {}
        cursor = 0
        for entry in (data.get("fixtures") or []):
            if not isinstance(entry, dict):
                continue
            starts = " ".join(str(entry.get("starts_with") or "").split())
            at = _locate(chunk, starts, cursor)
            if at == -1:
                continue
            cursor = at + 1
            found.append((chunk, at, " ".join(str(entry.get("label") or "").split())[:60]))

    # Cut each chunk at the offsets found in it; a fixture runs to the next one's start.
    blocks = []
    by_chunk = {}
    for chunk, at, label in found:
        by_chunk.setdefault(id(chunk), (chunk, []))[1].append((at, label))
    for chunk, marks in by_chunk.values():
        marks.sort()
        for i, (at, label) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(chunk)
            body = chunk[at:end].strip()
            if body:
                blocks.append((label, body))
    return _dedupe_fixtures(blocks)


def _dedupe_fixtures(blocks):
    """Drops fixtures already seen, keeping the first and longest version of each.

    Two things make duplicates, and both are ordinary rather than exotic. A document is read
    in slices, so anything spanning a boundary is read twice. And a requirement table repeats
    its header - sometimes whole rows - on every page.

    Matched on the opening of the requirement text rather than the whole of it, because the
    second copy is often TRUNCATED at a slice boundary and so is not byte-identical to the
    first. A document's own reference code, where it gives one, is stronger still: two entries
    labelled ED-75 are the same product however their text differs.
    """
    seen_labels = set()
    seen_openings = {}
    out = []
    for label, body in blocks:
        key_label = re.sub(r"[^a-z0-9]", "", (label or "").lower())
        opening = re.sub(r"[^a-z0-9]", "", body.lower())[:70]
        if key_label and key_label in seen_labels:
            continue
        if opening and opening in seen_openings:
            # Keep whichever copy is longer - the truncated one is the boundary artefact.
            index = seen_openings[opening]
            if len(body) > len(out[index][1]):
                out[index] = (label or out[index][0], body)
            continue
        if key_label:
            seen_labels.add(key_label)
        if opening:
            seen_openings[opening] = len(out)
        out.append((label, body))
    return out


def _split_looks_broken(text, blocks) -> bool:
    """Does the rule-based split look like it failed to separate the fixtures?

    The failure it is looking for is specific and recognisable: a long document coming back
    as one or two enormous blocks, which is what happens when the wording the splitter keys
    on ("Supply of", or blank lines between fixtures) is not how this document is written.
    A dozen ordinary-sized blocks are not that.
    """
    text = text or ""
    if not blocks:
        return bool(text.strip())

    # Measured on both cases rather than guessed, because a wrong threshold here either
    # wastes three minutes on a good document or leaves a broken one broken:
    #
    #                                     correctly split (52)   under-split (12 in one)
    #     fixture labels in one block              0                      12
    #     distinct colour temperatures             2                      12
    #     distinct wattages                        6                      12
    #     longest block                         2579 chars              562 chars
    #
    # Labels are decisive and wattages are useless - a single real fixture legitimately
    # offers six of them. Length is useless too: the broken document here is SHORT. An
    # earlier version used three distinct colour temperatures, which one real fixture hits
    # on its own by offering 2700K/3000K/4000K, and that sent a perfectly-split document off
    # for three minutes of re-reading.
    for block in blocks:
        labels = re.findall(r"\b(?:Type|Ref|Fixture)\.?\s*[A-Z]{0,3}\s*\d{1,3}\b", block, re.I)
        ccts = set(re.findall(r"\b\d{4}\s*K\b", block, re.I))
        if len(labels) >= 3 or len(ccts) >= 5:
            return True

    if len(text) > 3000 and len(blocks) <= 2:
        return True
    longest = max(len(b) for b in blocks)
    # One block carrying most of a long document means the others are scraps around it.
    return len(text) > 6000 and longest > 0.6 * len(text)


def choose_blocks(text, regex_blocks, force=False):
    """-> (blocks, labels, source) - the better of the regex split and the model's.

    "Better" is measured, not assumed: whichever separated MORE fixtures wins. That sounds
    crude and is surprisingly hard to fool, because the failure this exists for is a splitter
    producing ONE block from a document containing twelve fixtures. A split that finds more
    distinct fixtures in the same text has genuinely found structure the other missed.

    It matters that this is a comparison rather than a preference. Measured on the real
    52-fixture Studio document, the regexes find all 52 and the model finds 17 - it merges
    fixtures whose wording is nearly identical, which is exactly the case a BOQ must keep
    apart. Letting the model take over there would have quietly dropped two thirds of the
    document. On a document whose wording the regexes do not recognise, the comparison goes
    the other way, and that is the whole point of offering it.
    """
    if not is_enabled():
        return regex_blocks, [""] * len(regex_blocks), "off"
    if not force and not _split_looks_broken(text, regex_blocks):
        # Only reachable when force is False. The rules clearly separated the fixtures, so
        # re-reading the document costs about a
        # minute to confirm what is already known. Measured on the real 52-fixture Studio
        # document: the model was asked, found 17, the regex split was kept, and the only
        # result was 68 seconds of waiting. Ticking the box should still improve the FIELDS
        # (augment_batch runs regardless) - it just should not re-litigate a good split.
        return regex_blocks, [""] * len(regex_blocks), "regex-looked-fine"
    found = split_document(text)
    # No ratio ceiling here. One was tried - reject a split more than six times the
    # rule-based count - and it was wrong at both ends: it rejected the very case this
    # exists for (rules find 1, model finds 15) and would not have caught the runaway it
    # was written for (64 against 11 is under the threshold). The runaway was duplicates
    # from reading the document in slices, and _dedupe_fixtures deals with that at the
    # level it actually happens, rather than inferring it from a count.
    if len(found) > len(regex_blocks):
        return [b for _, b in found], [lab for lab, _ in found], "llm"
    # The model was asked and did not separate more fixtures, so the rule-based split stands.
    # Reported distinctly from "never asked" - the two look identical from outside otherwise,
    # which is exactly how "is it even using the model?" becomes an unanswerable question.
    return regex_blocks, [""] * len(regex_blocks), "llm-declined"
