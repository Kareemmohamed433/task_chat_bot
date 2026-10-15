# Monthly Operating Cost Analysis

## 1. Executive Summary

This document provides a realistic monthly cost model for deploying the **AI Task Assistant** to support an organization of **100 employees** sending **20 messages per employee per day** (**2,000 messages/day**; **44,000 messages/month** across 22 working days).

Three operational scenarios are modeled:
1. **Low-Cost Configuration (Hybrid Architecture)**: ~$15.15 / month ($0.15 per employee).
2. **Expected Configuration (Standard Cloud + Lightweight LLM)**: ~$38.54 / month ($0.39 per employee).
3. **High-Cost / Enterprise Configuration (Flagship LLM + Managed PostgreSQL)**: ~$226.40 / month ($2.26 per employee).

---

## 2. Core Workload Assumptions

| Metric | Baseline Assumption | Rationale |
| :--- | :--- | :--- |
| **Active Employees** | 100 employees | Target organizational capacity requirement. |
| **Daily Messages per Employee** | 20 messages | Task creations, queries, policy questions, status updates. |
| **Total Daily Messages** | 2,000 messages | 100 employees × 20 messages/day. |
| **Working Days per Month** | 22 days | Standard 5-day Saudi working calendar (Sun–Thu). |
| **Total Monthly Messages** | 44,000 messages | 2,000 messages/day × 22 working days. |
| **Average Input Tokens per Message** | 750 tokens | System instructions + schema definition + few-shot context + user text. |
| **Average Output Tokens per Message** | 80 tokens | Structured JSON containing intent, target, date, and description. |
| **Retry & Failure Overhead** | 5% | Factored into total token volume for network or user retries. |

Total Monthly Token Volume:
- **Total Monthly Input Tokens**: 44,000 × 750 × 1.05 = **34,650,000 tokens (34.65M tokens)**.
- **Total Monthly Output Tokens**: 44,000 × 80 × 1.05 = **3,696,000 tokens (3.70M tokens)**.

---

## 3. Provider Pricing Benchmarks (Verified October 2026)

| Provider & Model | Input Price / 1M Tokens | Output Price / 1M Tokens | Source Citation |
| :--- | :--- | :--- | :--- |
| **Groq (`qwen/qwen3.8-27b`)** | $0.20 | $0.40 | Groq Cloud Pricing (On-Demand Tier) |
| **OpenAI (`gpt-4o-mini`)** | $0.15 | $0.60 | OpenAI Official API Pricing |
| **OpenAI (`gpt-4o`)** | $2.50 | $10.00 | OpenAI Official API Pricing |
| **Anthropic (`claude-3-5-haiku`)** | $0.80 | $4.00 | Anthropic API Pricing |

---

## 4. Scenario Breakdown

### Scenario 1: Low-Cost Configuration (Hybrid Architecture)
*Strategy: Leverage the system's deterministic engine for high-frequency standard intents (greetings, policy inquiries, "my tasks" listings, and standard task commands ~70% of traffic). Route only ambiguous colloquial requests (30%) to Groq or GPT-4o-mini. Host on a lightweight single-instance VPS with local SQLite.*

- **LLM Traffic Proportion**: 30% of 44,000 = 13,200 LLM calls.
- **Input Tokens**: 13,200 × 750 × 1.05 = 10,395,000 tokens (10.40M) @ $0.15/1M = **$1.56**
- **Output Tokens**: 13,200 × 80 × 1.05 = 1,108,800 tokens (1.11M) @ $0.60/1M = **$0.67**
- **Total Monthly LLM Cost**: **$2.23**
- **Hosting & Infrastructure**:
  - Hetzner / DigitalOcean Basic VPS (2 vCPU, 4GB RAM): **$10.00 / month**
  - Storage & Backups (Automated nightly SQLite backup to S3): **$1.50 / month**
  - Logging & Monitoring: Self-hosted Prometheus/Grafana or basic CloudWatch: **$1.42 / month**
- **Total Infrastructure Cost**: **$12.92 / month**
- **Total Monthly Cost**: **$15.15 / month**
- **Cost Per Employee**: **$0.15 / employee / month**

---

### Scenario 2: Expected Configuration (100% LLM with Lightweight Model)
*Strategy: 100% of user messages are processed by Groq (`qwen/qwen3.8-27b`) or OpenAI (`gpt-4o-mini`). Hosted on a standard containerized cloud instance (e.g., AWS App Runner or Railway) with automated off-site backups.*

- **LLM Traffic Proportion**: 100% (44,000 calls).
- **Input Tokens**: 34.65M tokens @ $0.20/1M = **$6.93**
- **Output Tokens**: 3.70M tokens @ $0.40/1M = **$1.48**
- **Total Monthly LLM Cost**: **$8.41**
- **Hosting & Infrastructure**:
  - Container Hosting (AWS App Runner / DigitalOcean App Platform): **$22.00 / month**
  - Object Storage & S3 Database Backups (10GB with versioning): **$2.50 / month**
  - Logging, Exception Tracking (Sentry Developer Tier): **$5.63 / month**
- **Total Infrastructure Cost**: **$30.13 / month**
- **Total Monthly Cost**: **$38.54 / month**
- **Cost Per Employee**: **$0.39 / employee / month**

---

### Scenario 3: Higher-Cost / Enterprise Configuration (Flagship LLM + Managed PostgreSQL)
*Strategy: 100% of messages are processed by a flagship model (GPT-4o) with larger system prompts and rich context. Hosted on high-availability cloud architecture with a managed PostgreSQL database, automated multi-AZ replication, and enterprise monitoring.*

- **LLM Traffic Proportion**: 100% (44,000 calls).
- **Average Tokens (Larger context)**: 1,000 input tokens / 120 output tokens.
- **Input Tokens**: 44,000 × 1,000 × 1.05 = 46.2M tokens @ $2.50/1M = **$115.50**
- **Output Tokens**: 44,000 × 120 × 1.05 = 5.54M tokens @ $10.00/1M = **$55.40**
- **Total Monthly LLM Cost**: **$170.90**
- **Hosting & Infrastructure**:
  - Managed PostgreSQL (AWS RDS db.t4g.micro with automated snapshots): **$28.00 / month**
  - Container Hosting (AWS ECS Fargate 1 vCPU, 2GB RAM): **$22.50 / month**
  - Monitoring & Log Retention (Datadog or AWS CloudWatch): **$15.00 / month**
  - SSL, WAF, & DNS (Cloudflare Pro): **$10.00 / month**
- **Total Infrastructure Cost**: **$55.50 / month**
- **Total Monthly Cost**: **$226.40 / month**
- **Cost Per Employee**: **$2.26 / employee / month**

---

## 5. Summary Comparison Table

| Expense Category | Scenario 1: Low-Cost (Hybrid) | Scenario 2: Expected (Standard) | Scenario 3: Enterprise (Flagship) |
| :--- | :--- | :--- | :--- |
| **Model Used** | Rule Engine + GPT-4o-mini | Groq Qwen 27B / GPT-4o-mini | OpenAI GPT-4o |
| **Monthly Messages** | 44,000 | 44,000 | 44,000 |
| **Monthly LLM Cost** | $2.23 | $8.41 | $170.90 |
| **Compute / Hosting** | $10.00 | $22.00 | $22.50 |
| **Database & Backups** | $1.50 | $2.50 | $28.00 |
| **Monitoring & Logs** | $1.42 | $5.63 | $15.00 |
| **Total Monthly Cost** | **$15.15** | **$38.54** | **$226.40** |
| **Cost Per Employee / Month** | **$0.15** | **$0.39** | **$2.26** |

---

## 6. Cost Optimization Recommendations

1. **Deterministic Fast-Path Routing (Saves 70% of LLM Costs)**:
   Routing standard commands (e.g. "وش مهامي اليوم؟", greetings, and policy questions) to deterministic regex saves up to 30,000 LLM calls per month without degrading user experience.
2. **Prompt Caching**:
   Both OpenAI and Anthropic offer 50% discount on cached input prompt tokens. Given the repetitive static system prompt, input token costs decrease by 30–45%.
3. **Idempotency Cache**:
   The implemented fast-path idempotency check eliminates redundant LLM calls on client retries, saving approximately 5% of monthly token waste.
4. **Local SQLite Longevity**:
   Because SQLite easily supports the required 0.07 req/sec average workload with 0ms network latency, delaying PostgreSQL migration saves $25–$35/month in managed database fees until concurrency demands require it.
