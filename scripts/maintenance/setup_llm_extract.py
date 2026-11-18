"""Turns on the Match tab's language-model fallback, and proves it works before you rely on it.

    python scripts/maintenance/setup_llm_extract.py --check                 what is configured right now
    python scripts/maintenance/setup_llm_extract.py --key <your-groq-key>   write it to .env (read by run.bat)
    python scripts/maintenance/setup_llm_extract.py --key <k> --dev         write it to .env.development instead
    python scripts/maintenance/setup_llm_extract.py --test                  make one real call and show the result

The key is written to `.env`, which run.bat reads - the server on the real local database.
Add --dev to target .env.development (run_dev.bat's sandbox) instead. Both are gitignored,
so neither can be committed by accident, and the key is never printed back: `--check`
reports only whether a value is present, its length and its first four characters.

--test is the part that matters. It runs a requirement paragraph written in a style the
regexes handle badly through the whole path and shows what each stage found, so you can see
the model earning its place rather than take it on trust.
"""
# --- repo-root shim ------------------------------------------------------------------
# Running a script file puts ITS OWN directory on sys.path, not the repo root, so `app`
# would not import and a `Path(__file__).parent` would no longer be the project root.
# Both are fixed here by walking up to the directory that contains app/ - the same rule
# app/database.py uses. Run this script from anywhere; paths below stay repo-relative.
import pathlib as _pathlib, sys as _sys
_REPO_ROOT = next((p for p in _pathlib.Path(__file__).resolve().parents
                   if (p / "app").is_dir()), None)
if _REPO_ROOT is None:
    raise SystemExit("Cannot find the repo root: no parent directory contains app/.")
_sys.path.insert(0, str(_REPO_ROOT))
# --- end shim ------------------------------------------------------------------------
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = _REPO_ROOT
# .env, not .env.development: run.bat (the real local database, port 8000) reads .env, and
# that is the server people actually use. run_dev.bat's sandbox reads .env.development and
# will pick the key up from there if you would rather keep the two separate. Both are
# gitignored, so neither can be committed by accident.
ENV_FILE = BASE / (".env" if "--dev" not in sys.argv else ".env.development")

# Deliberately written the way a different practice would write it - the case the regexes
# are weak on, which is the only reason this feature exists.
SAMPLE = ("A compact recessed downlight of approximately seven watts, warm white at 3000K, "
          "with a narrow 24 degree distribution, colour rendering Ra 90 minimum, suitable "
          "for a bathroom zone so IP54, running on a DALI control system, matt white "
          "die-cast aluminium body, five year warranty.")


def load_env_file():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def write_env_values(updates):
    """Rewrites only the named keys, leaving every other line exactly as it was."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def check():
    values = load_env_file()
    print(f"{ENV_FILE.name}: {'present' if ENV_FILE.exists() else 'MISSING'}")
    for key in ("LLM_EXTRACT_ENABLED", "GROQ_API_KEY", "LLM_EXTRACT_MODEL",
                "LLM_EXTRACT_MIN_FIELDS"):
        raw = values.get(key)
        if raw is None:
            print(f"   {key:<24} not set")
        elif key.endswith("KEY"):
            # Never the value. Length and shape are enough to tell a real key from a typo.
            print(f"   {key:<24} set, {len(raw)} chars, starts {raw[:4]!r}")
        else:
            print(f"   {key:<24} {raw}")
    for key, value in values.items():
        os.environ.setdefault(key, value)
    from app import llm_extract
    print(f"\nfeature reports itself: {'ON' if llm_extract.is_enabled() else 'OFF'}")
    print("(both LLM_EXTRACT_ENABLED and GROQ_API_KEY must be set for it to be on)")


def test():
    for key, value in load_env_file().items():
        os.environ.setdefault(key, value)
    from app import llm_extract
    from app.requirement_matcher import extract_requirement_fields, FIELD_WEIGHTS
    scored = tuple(FIELD_WEIGHTS)

    print("A requirement written in an unfamiliar style:\n")
    print(f"   {SAMPLE}\n")

    regex_only = extract_requirement_fields(SAMPLE)
    n_regex = sum(1 for f in scored if regex_only.get(f))
    print(f"1. regexes alone: {n_regex}/{len(scored)} scored fields")
    for f in scored:
        if regex_only.get(f):
            print(f"      {f:<14} {regex_only[f]}")

    if not llm_extract.is_enabled():
        print("\n2. the model is OFF, so this is all the Match tab would use.")
        print("   Run:  python scripts/maintenance/setup_llm_extract.py --key <your-groq-key>")
        return

    print("\n2. asking the model about it (one real API call)...")
    import time
    t0 = time.time()
    merged, used = llm_extract.augment(regex_only, SAMPLE, scored)
    took = time.time() - t0
    if not used:
        print(f"   the model added nothing after {took:.1f}s.")
        print("   Either the regexes were already good enough (see the threshold,")
        print("   LLM_EXTRACT_MIN_FIELDS), or the call failed - which is deliberately")
        print("   silent, because a failure must never break the Match tab. Re-run")
        print("   with --raw to see the call itself.")
        return
    added = {k: v for k, v in merged.items() if k not in regex_only or not regex_only.get(k)}
    print(f"   answered in {took:.1f}s and ADDED {len(added)} field(s):")
    for k, v in added.items():
        print(f"      {k:<14} {v}")
    n_after = sum(1 for f in scored if merged.get(f))
    print(f"\n   scored fields: {n_regex}/{len(scored)} -> {n_after}/{len(scored)}")
    kept = all(merged[k] == v for k, v in regex_only.items() if v)
    print(f"   every value the regexes found was preserved: {kept}")


def raw():
    """The unfiltered call, for when --test says the model added nothing and you want to
    know whether that was the threshold or a failure."""
    for key, value in load_env_file().items():
        os.environ.setdefault(key, value)
    from app import llm_extract
    if not llm_extract.is_enabled():
        print("Feature is off - set LLM_EXTRACT_ENABLED and GROQ_API_KEY first.")
        return
    import httpx
    payload = {
        "model": os.environ.get("LLM_EXTRACT_MODEL") or llm_extract.DEFAULT_MODEL,
        "messages": [{"role": "system", "content": llm_extract.SYSTEM_PROMPT},
                     {"role": "user", "content": SAMPLE}],
        "temperature": 0, "response_format": {"type": "json_object"}, "max_tokens": 800,
    }
    try:
        r = httpx.post(llm_extract.API_URL, json=payload, timeout=30,
                       headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                                "Content-Type": "application/json"})
        print(f"HTTP {r.status_code}")
        if r.status_code != 200:
            # The body of an error explains a bad key or a spent quota, and contains no secret.
            print(r.text[:600])
            return
        body = r.json()
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        print(f"model: {body.get('model')}")
        usage = body.get("usage") or {}
        print(f"tokens: {usage.get('prompt_tokens')} in, {usage.get('completion_tokens')} out")
        print(f"\nraw content:\n{content}")
        print(f"\nafter this module's filtering:\n{llm_extract._parse_response(content)}")
    except Exception as e:
        print(f"call failed: {type(e).__name__}: {e}")


def main():
    args = sys.argv[1:]
    if "--key" in args:
        key = args[args.index("--key") + 1]
        write_env_values({"GROQ_API_KEY": key, "LLM_EXTRACT_ENABLED": "1"})
        print(f"Wrote GROQ_API_KEY and LLM_EXTRACT_ENABLED=1 to {ENV_FILE.name}")
        print("(gitignored - it will not be committed)")
        print("\nNow run:  python scripts/maintenance/setup_llm_extract.py --test")
        return
    if "--test" in args:
        test()
        return
    if "--raw" in args:
        raw()
        return
    check()
    print("\n  --key <k>   write the key    --test   try it    --raw   see the call itself")


if __name__ == "__main__":
    main()
