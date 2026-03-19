---
name: driodrun-patterns
description: Coding patterns extracted from the AEO DroidRun Orchestrator Python project
version: 1.0.0
source: local-code-analysis
analyzed_commits: 0
---

# DroidRun Orchestrator Patterns

Single-entry-point orchestrator for parallel Android device automation.
Uses DeepSeek LLM + llama-index for prompt generation and DroidRun for device control.

## Project Architecture

```
driodrun/
├── main.py                  # Single entry point — orchestration, CLI, state, logging
├── agents/
│   ├── prompt_generator.py  # LLM-based content generation (DeepSeek via llama-index)
│   ├── session_runner.py    # Android device execution via DroidRun
│   └── ranking_auditor.py   # Audit prompt generation (pure Python, no LLM)
├── config/
│   └── config.yaml          # DroidRun agent config with per-role LLM profiles
├── clients.json             # Client data (id, biz_name, city, state, keywords)
├── device_assignments.json  # client-to-device mapping
└── device_rotation.json     # daily run tracking (one device = one session/day)
```

**Principle**: Business logic lives in `main.py`; agents/ modules are pure execution
utilities with no orchestration knowledge. Agents do one thing each.

## Asyncio Parallel Execution with Stagger

```python
async def run_parallel(sessions, stagger_seconds=5):
    """Run N sessions concurrently, starting each one stagger_seconds apart."""
    tasks = [
        _staggered_session(s, delay=i * stagger_seconds)
        for i, s in enumerate(sessions)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    final = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            # Exception is surfaced, not raised — degrade gracefully
            final.append({"success": False, "device_id": sessions[i].get("device_id"), "output": str(r)})
        else:
            final.append(r)
    return final
```

**Key points**:
- `asyncio.gather(*tasks, return_exceptions=True)` — never lets one failure kill others
- Stagger via `asyncio.sleep(delay)` inside each coroutine, not between `gather` calls
- Results are parallel-aligned with input sessions (zip-safe)

## Safe JSON File Load Pattern

```python
def load_rotation():
    try:
        with open(ROTATION_FILE, "r") as f:
            return json.load(f)
    except:
        return {}   # file missing or corrupt → return safe default
```

Always use bare `except` (not `except Exception`) only when you own the recovery path.
For write operations, always succeed or raise — never silently skip writes.

## Device Pool + Daily Rotation State

```python
DEVICE_POOL = {
    "device-001": {"serial": "..."},
    "device-002": {"serial": "..."},
}

def is_device_used_today(device_id):
    today_rot = get_today_rotation()
    return len(today_rot.get(device_id, {})) > 0

def get_free_devices():
    return [d for d in DEVICE_POOL if not is_device_used_today(d)]
```

State key: `rotation[date][device_id][client_id:keyword] = {status, timestamp, ...}`
One device per day enforced by checking entry count, not a flag.

## LLM Integration via llama-index (DeepSeek)

```python
from llama_index.llms.deepseek import DeepSeek
from llama_index.core.llms import ChatMessage, MessageRole

def _get_llm():
    return DeepSeek(
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        temperature=0.85,
    )

messages = [
    ChatMessage(role=MessageRole.SYSTEM, content=SYSTEM_PROMPT),
    ChatMessage(role=MessageRole.USER, content=user_msg),
]
response = llm.chat(messages)
return response.message.content.strip()
```

- API key read from env at call time, not at module import
- LLM instance created per-call (stateless, no connection pooling concerns)
- Temperature tuned per task: 0.0 for agent execution, 0.85 for creative generation

## YAML Config with Per-Role LLM Profiles

```yaml
llm_profiles:
  fast_agent:
    provider: DeepSeek
    model: deepseek-chat
    temperature: 0.0
  text_manipulator:
    provider: DeepSeek
    model: deepseek-chat
    temperature: 0.8
```

Different roles get different temperatures — deterministic for execution, creative for content.
Config is loaded by DroidRun: `DroidrunConfig.from_yaml(CONFIG_PATH)`.

## CLI Pattern with Async Dispatch

```python
def main():
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("--audit",  action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reset",  action="store_true")
    args = parser.parse_args()

    # Validate env first, before any async work
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("⚠️  DEEPSEEK_API_KEY not set!")
        sys.exit(1)

    if args.status:
        show_status()              # sync
    elif args.reset:
        reset_rotation()           # sync
    elif args.audit:
        asyncio.run(run_audit())   # async
    else:
        asyncio.run(run_daily())   # async
```

Pattern: validate all required env vars before dispatching to async code.

## Session Planning: Round-Robin Across Clients

```python
def plan_sessions():
    remaining = []
    max_keywords = max(len(c["keywords"]) for c in clients)
    for i in range(max_keywords):
        for client in clients:
            if i < len(client["keywords"]):
                kw = client["keywords"][i]
                if not is_keyword_done_today(client["id"], kw):
                    remaining.append((client, kw))
```

Round-robin by keyword index across clients ensures fairness — no single client dominates
when there are fewer devices than total keywords.

## External Process via subprocess (ADB)

```python
result = subprocess.run(
    ["adb", "-s", serial, "shell", "pm", "clear", "com.android.chrome"],
    capture_output=True, text=True, timeout=10,
)
```

- Always use list form (not string) to avoid shell injection
- Always set `timeout`
- Always check `returncode` and log stderr on failure

## Logging Pattern

Each session appended to a JSON array log file:
```python
try:
    with open(LOG_FILE, "r") as f:
        logs = json.load(f)
except:
    logs = []

logs.append(entry)
with open(LOG_FILE, "w") as f:
    json.dump(logs, f, indent=2)
```

Simple append-to-array log. Not thread-safe for concurrent writes — acceptable because
each device runs in its own coroutine and logs after completion (not during).
