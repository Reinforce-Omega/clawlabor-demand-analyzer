# Demand Analyzer Worker — Design Document

**Author:** ClawLabor Platform Team  
**Date:** 2026-05-15  
**Status:** Draft

---

## 1. Background

The ClawLabor agent market lets users pay agents to complete tasks. When no existing agent can satisfy a user's request, the market records it as an **unmet demand** and publishes it to a Redis stream. This document describes the **Demand Analyzer Worker** — a service that consumes those events, uses an LLM to analyze and summarize them into actionable agent proposals, and delivers the results to a Lark workspace for human review and follow-up.

---

## 2. Goals

- Consume unmet demand events from a Redis stream reliably (at-least-once delivery).
- Use an LLM to extract structured insight from raw user task descriptions.
- Deliver structured analysis to a designated Lark channel/bot in near real-time.
- Be horizontally scalable: multiple worker instances must not duplicate messages.

## 3. Non-Goals

- Automatically creating or deploying new agents (human decision required).
- Training or fine-tuning any model.
- Storing historical demand trends (out of scope for v1; a future analytics layer can read from the same stream).

---

## 4. Terminology

| Term | Meaning |
|---|---|
| **Unmet demand** | A user task for which no existing agent was matched |
| **Demand event** | The Redis stream message representing one unmet demand |
| **Consumer group** | Redis Streams mechanism that distributes messages across worker instances |
| **Lark** | Feishu/Lark collaboration platform; notifications are sent via its webhook or bot API |
| **LLM analysis** | The structured output produced by passing the demand description through a language model |

---

## 5. System Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                        Agent Market Backend                        │
│  ┌──────────────┐   Publishes unmet demand   ┌──────────────────┐ │
│  │  Task Router │ ─────────────────────────▶ │  Redis Stream    │ │
│  └──────────────┘                            │  (demands:unmet) │ │
└───────────────────────────────────────────────┴──────────────────┘
                                                        │
                                        XREADGROUP (consumer group)
                                                        │
                              ┌─────────────────────────▼──────────┐
                              │       Demand Analyzer Worker        │
                              │                                      │
                              │  ┌────────────┐  ┌───────────────┐ │
                              │  │  Consumer  │  │  LLM Client   │ │
                              │  │  (Redis)   │─▶│  (Anthropic)  │ │
                              │  └────────────┘  └───────┬───────┘ │
                              │                          │          │
                              │                  ┌───────▼───────┐  │
                              │                  │  Lark Notifier│  │
                              │                  └───────────────┘  │
                              └─────────────────────────────────────┘
                                                        │
                                              Lark Bot API / Webhook
                                                        │
                                              ┌─────────▼────────┐
                                              │   Lark Channel   │
                                              │  (ops/agent-dev) │
                                              └──────────────────┘
```

---

## 6. Data Model

### 6.1 Demand Event (Redis Stream Message)

Each message published to the stream contains the following fields:

| Field | Type | Description |
|---|---|---|
| `agent_id` | UUID string | The agent that was matched (or a null/sentinel UUID if none matched) |
| `description` | string | Raw task description submitted by the user |
| `signal_type` | string | Source channel of the demand (see §6.2) |
| `user_id` | string | Anonymized user identifier (for dedup, not PII) |
| `created_at` | ISO-8601 timestamp | When the demand was recorded |

### 6.2 LLM Analysis Output

The LLM is prompted to return a structured JSON object:

```json
{
  "category": "string",           // e.g. "data processing", "content generation"
  "complexity": "low|medium|high",
  "required_capabilities": ["string"],  // skills the ideal agent would need
  "suggested_agent_name": "string",
  "suggested_agent_description": "string",
  "input_schema": {
    "fields": [
      {
        "name": "string",         // argument name
        "type": "string",         // e.g. "string", "number", "boolean", "url"
        "description": "string",  // what the argument represents
        "required": true          // whether the argument is mandatory
      }
    ]
    // Note: file arguments use type "url" — the caller provides a URL to the file
  },
  "output_schema": {
    "fields": [
      {
        "name": "string",         // output field name
        "type": "string",         // e.g. "string", "number", "url"
        "description": "string"   // what is delivered to the user
        // Note: file outputs use type "url" — the agent returns a URL to the result file
      }
    ]
  },
  "priority_score": 0.0,          // 0–1, LLM-estimated demand urgency/value
  "reasoning": "string"           // brief rationale
}
```

### 6.3 Lark Notification Payload

The Lark message is an **interactive card** with:

- Header: `[New Unmet Demand]` + category + priority badge
- Body fields: complexity, required capabilities, suggested agent name/description, input/output schema
- Footer: link to the demand record, timestamp, `agent_id`
- Action button: "Create Agent" (deep-link to the agent creation form, pre-filled)

---

## 7. Component Design

### 7.1 Redis Stream Consumer

**Stream name:** `demands:unmet`  
**Consumer group:** `demand-analyzer`  
**Consumer name:** `worker-{instance-id}` (unique per process)

Key behaviors:

- Uses `XREADGROUP` with `COUNT=10` and `BLOCK=2000ms` to batch-read up to 10 messages per poll.
- Processes each message individually before ACKing (`XACK`). ACK happens **after** the Lark notification is confirmed sent, ensuring at-least-once delivery end-to-end.
- On startup, claims pending messages older than a configurable `claim_threshold` (default: 5 minutes) via `XAUTOCLAIM` to recover from crashed instances.
- Dead-letter: messages that fail after `max_retries` (default: 3) are moved to a `demands:dead-letter` stream with an error annotation.

### 7.2 LLM Analysis Module

**Model:** Claude Sonnet (configurable via `LLM_MODEL` env var)  
**Interface:** Anthropic Messages API with structured JSON output (tool use / JSON mode)

Prompt structure:

```
System:
  You are an agent marketplace analyst. Your job is to analyze unmet user demands
  and propose new agents that could fulfill them. Always respond with valid JSON
  matching the specified schema.

User:
  Analyze the following unmet demand and return a structured proposal.

  Task description: {description}
```

Design decisions:

- Use **tool use / function calling** to enforce the output schema and avoid parsing fragile free-text JSON.
- Apply **prompt caching** on the system prompt (it is static) to reduce latency and cost at scale.
- Set `max_tokens=1024`; the analysis is concise by design.
- Timeout: 30 seconds. On timeout, retry once; on second failure, write to dead-letter.

### 7.3 Lark Notifier

**Delivery method:** Lark Bot API (`/open-apis/im/v1/messages`) with `msg_type=interactive` (card).

- Authenticates using a bot app token stored in environment secrets.
- Posts to a configurable `LARK_CHANNEL_ID`.
- Retries up to 3 times with exponential backoff on non-2xx responses.
- On permanent failure (e.g. invalid token), logs the error and does **not** ACK the Redis message so a human can intervene.

### 7.4 Worker Process

```
for each poll cycle:
  1. XREADGROUP — read up to 10 messages
  2. for each message:
     a. Deserialize demand event fields
     b. Call LLM analysis module → structured result
     c. Call Lark notifier → send card
     d. XACK the message
     e. On any unrecoverable error → increment retry counter;
        if retries exhausted → XADD to dead-letter stream, then XACK
  3. Sleep until next BLOCK returns or timeout
```

---

## 8. Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `REDIS_URL` | Yes | — | Redis connection string |
| `REDIS_STREAM_NAME` | No | `demands:unmet` | Source stream name |
| `REDIS_CONSUMER_GROUP` | No | `demand-analyzer` | Consumer group name |
| `REDIS_CLAIM_THRESHOLD_MS` | No | `300000` | PEL claim age threshold (ms) |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `LLM_MODEL` | No | `claude-sonnet-4-6` | Model ID for analysis |
| `LARK_APP_ID` | Yes | — | Lark bot App ID |
| `LARK_APP_SECRET` | Yes | — | Lark bot App Secret |
| `LARK_CHANNEL_ID` | Yes | — | Target Lark channel/chat ID |
| `WORKER_BATCH_SIZE` | No | `10` | Messages per XREADGROUP call |
| `WORKER_BLOCK_MS` | No | `2000` | XREADGROUP block timeout (ms) |
| `MAX_RETRIES` | No | `3` | Max per-message retry attempts |

---

## 9. Error Handling & Reliability

| Failure Scenario | Behavior |
|---|---|
| Redis connection lost | Worker retries with exponential backoff; logs alert if down > 60s |
| LLM API timeout (once) | Retry once immediately |
| LLM API timeout (twice) | Move to dead-letter, XACK original |
| LLM returns malformed JSON | Log + dead-letter; do not retry (deterministic failure) |
| Lark API rate limit (429) | Respect `Retry-After` header; pause that message, continue others |
| Lark API auth failure (401) | Stop worker, emit critical alert (requires human intervention) |
| Worker process crash | On restart, XAUTOCLAIM reclaims pending messages |

---

## 10. Scalability

- Worker instances are stateless beyond Redis consumer group membership; scale horizontally by adding more instances.
- Redis Streams natively distributes messages across consumers in the same group — no additional coordination layer needed.
- LLM calls are the primary latency bottleneck; each instance can process messages concurrently (async I/O or thread pool) to maximize throughput.
- At high volume, batching multiple demands into a single LLM prompt (multi-demand analysis) can reduce API cost; deferred to v2.

---

## 11. Observability

- **Structured logs** (JSON) for every message: `message_id`, `llm_latency_ms`, `lark_status`, `retry_count`.
- **Alerting triggers (log-based):** dead-letter events; Lark auth failure (401).
- Metrics instrumentation (Prometheus/statsd counters and histograms) is deferred to v2.

---

## 12. Security Considerations

- API keys (`ANTHROPIC_API_KEY`, `LARK_APP_SECRET`) stored in a secrets manager (e.g. AWS Secrets Manager, Vault); injected at runtime — never committed to source.
- User task descriptions may contain PII; the worker forwards them to the LLM API and Lark. Ensure both are within the platform's data processing agreement scope.
- Redis stream ACL: the worker's Redis user has `XREADGROUP`, `XACK`, `XAUTOCLAIM`, `XADD` (dead-letter only) permissions — no write access to the source stream.

---

## 13. Design Decisions

| Topic | Decision | Rationale |
|---|---|---|
| **Deduplication** | Not implemented in v1 | Volume is low enough that duplicate analysis noise is acceptable |
| **Feedback loop** | None required | Once the target agent is deployed it will match future similar demands, so no explicit "resolved" signal is needed |
| **Multi-demand batching** | Each demand is analyzed individually | Maximizes accuracy; batching can be reconsidered in v2 if cost becomes a concern |
| **Dead-letter visibility** | Log-based alerting only | A separate Lark channel for dead-letters is unnecessary overhead at this stage |

---

## 14. Milestones

| Milestone | Deliverable |
|---|---|
| M1 | Redis stream consumer + basic structured logging |
| M2 | LLM analysis module with schema validation |
| M3 | Lark interactive card notification |
| M4 | Dead-letter handling + XAUTOCLAIM recovery |
| M5 | Load test at 10× expected peak; tune batch size and concurrency |
| v2 | Metrics instrumentation (Prometheus/statsd) |
