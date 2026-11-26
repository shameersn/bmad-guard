# Hosting plan: guard event stack on the company network

**Status:** plan only. Nothing here is built (decision D4). The pilot runs in local Docker
([README.md](README.md)). This plan is for the move to company AWS after the pilot.

## Goal

One shared stack for all developers, reachable only from the company network (VPN), so that events from all laptops
are in one place and the dashboards show the whole team. Same parts as the pilot: collector, ClickHouse, Grafana.

## Plan

| Topic | Plan |
|---|---|
| Account | Company AWS account, one small environment. The account owner is an IT matter. |
| Network | Private subnets. **No public IP.** Reached over the company VPN only. A security group allows 443 from the VPN range only. |
| TLS | An internal Application Load Balancer with an ACM certificate on a company domain name (for example `guard-events.<company-domain>`). TLS ends at the ALB. The collector listens on plain HTTP inside the VPC. The ALB forwards `/v1/logs` to the collector and Grafana on separate host names or paths. |
| Compute | Start with one VM (EC2) running the same Docker Compose stack, with ports bound to the private interface. Move to ECS or managed ClickHouse only if the load needs it. |
| Storage | An encrypted EBS volume for ClickHouse data. Encrypted backup bucket (see Backups). No S3 for events (ADR 0003). |
| Auth | The same as the pilot: one ingest key per developer, bcrypt hashes, user stamped from the key. Grafana: admin password in the company password manager. Grafana SSO is a later item. |
| Retention | 180 days (TTL on the table). Pending compliance approval (open item 2). |
| Backups | Daily `./backup.sh backup`, copied to an encrypted bucket with a 35-day rule, plus an EBS snapshot. A restore check every month (`./backup.sh verify`). |
| Key rotation | Owner runs `keys.py rotate` on a schedule (proposal: every 6 months) and at once on a leak or when a developer leaves. Steps are in the README. |
| Monitoring | Collector `health_check` (port 13133) behind the ALB target check. Alarms: collector down, ClickHouse disk above 80%, no events for 24 hours on a working day, backup older than 36 hours. A count of HTTP 401 on the ALB shows wrong or expired keys. |
| Logs of the stack itself | Container logs to CloudWatch, short retention. They must never hold event content (the events carry none, ADR 0003). |
| Data | Events hold guard, stage, verdict, reason code, path and SHA-256. No prompt text, no file content, no email (scrubbed). Still treat the store as internal data. Healthcare projects: ask compliance if path names count as sensitive. |

## Who owns what

| Area | Owner |
|---|---|
| The stack config in the repo, keys, rotation, backups (pilot) | Shameer |
| AWS account, network, VPN, ALB, ACM, DNS | IT contact (**TBD**) |
| Retention approval | Compliance owner (**TBD**) |
| Grafana access list | Shameer (pilot), then the owner IT names |
| Collector and ClickHouse upgrades | Shameer, with the re-test in the README |

## Move steps (when approved)

1. IT creates the VM, the volume, the security group, the ALB, the ACM certificate and the DNS name.
2. Copy `deploy/collector/` to the VM. Create `secrets.local.ini` there (`keys.py init`). Do not copy pilot passwords.
3. Start the stack, then run `run_live_tests.py` on the VM against `127.0.0.1`.
4. Add the real developers with `keys.py add`. Do not reuse pilot keys.
5. Developers re-run the setup script with the new endpoint (D5: one re-run). Events queued on laptops drain on the next send.
6. Test once through the ALB from a VPN laptop: a good key (200) and a bad key (401).
7. Turn on the alarms and the daily backup. Do the first restore check.

## Open items

| # | Item | Owner | When |
|---|---|---|---|
| 1 | Name the IT contact for AWS, VPN, ALB and DNS | Shameer | Before the hosting plan is final |
| 2 | Compliance approves the 180-day retention (and says if path names are sensitive) | Compliance owner (TBD), asked by Shameer | Before Gate 3 |
| 3 | Domain name for the endpoint | IT contact | Before the move |
| 4 | Key rotation period (proposal: 6 months) | Shameer | Before the move |
| 5 | Who owns the stack after the pilot (Shameer or a team) | Shameer with Manesh G | End of the pilot |
| 6 | Decide EC2 with Compose or a managed service | IT contact with Shameer | Before the move |
| 7 | Grafana access: password list or SSO | Shameer | Before more than the pilot group has access |
| 8 | Test the ALB path: body size limit, idle timeout, health check on `13133` | IT contact | During the move |

## Not decided here

- Cost. Not estimated. One small VM and one volume is the starting point.
- High availability. One VM is a single point of failure. The sender queues events, so a short outage loses nothing.
  A long outage is accepted for the pilot.
