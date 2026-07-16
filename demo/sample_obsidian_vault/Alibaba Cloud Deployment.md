---
title: Alibaba Cloud Deployment
tags: [haven, deployment]
---

# Alibaba Cloud Deployment

I decided to deploy [[Haven]] on Alibaba Cloud because the Qwen DashScope
integration behind the [[Query Rewriter]] and Manager AI cuts latency for a
same-region deployment — and because I want hackathon judges to experience a
live instance instead of localhost.

The provisioning script is written and sitting under `deploy/`, but it's blocked
on a pending security review before the demo link can point at a live instance. A
teammate is handling that review while I finish the [[Ontology V2]] migration.

Task: harden the provisioning script and get the security review scheduled before
the submission deadline.

See also: [[Haven]], [[Benchmark Results]].
