# Upstream review log

This fork no longer merges [ai-zerolab/mcp-email-server](https://github.com/ai-zerolab/mcp-email-server).
We review each upstream PR and port useful fixes by hand. This file and our commit
messages are the only record of those decisions. Port details belong in the commit
message, so notes here stay to a few words.

**Last reviewed:** #273 (`0f4a2de`, 2026-10-22). Reviewed 2026-10-24.

## How to review

1. Fetch upstream and list what is new since the last reviewed commit:

   ```bash
   git fetch upstream
   git log --no-merges --format='%h %cd %s' --date=short 0f4a2de..upstream/main
   ```

2. Add a row for each new PR, newest first, and update **Last reviewed**.
3. Port one fix per commit, with `(port of upstream #NNN)` in the commit subject.

**Decisions**

| Decision | Meaning |
|---|---|
| Ported | Reimplemented here; see the commit |
| Adapted | Handled a different way; see the commit |
| Already covered | We fixed or built the same thing independently |
| Deferred | Worth doing later; the note says when |
| Ruled out | We don't want it |
| Not applicable | Doesn't touch anything this fork has |
| Not reviewed | No decision yet |

## Fork history

| Date | Event |
|---|---|
| 2026-02-16 | Forked at upstream #115 |
| 2026-03-06 | Last merge from upstream, through #122 and #123 (`a6fc0f5`) |
| 2026-10-24 | Stopped merging. Upstream's V2 rewrite (#212) has no OAuth2 or filter rules |

## Decisions

Upstream PRs merged after our last merge (#122), newest first. Dates are upstream merge dates.

| PR | Upstream title | Merged | Decision | Our commit | Notes |
|---|---|---|---|---|---|
| #273 | fix: quote QQ IMAP SEARCH and configure metadata snapshots | 2026-10-22 | Not applicable |  | No CHARSET in our searches; snapshots need upstream's cache |
| #271 | fix: make attachment retrieval consistent with MIME listing | 2026-10-13 | Ported | cae54a5 | Attached-email inner export not ported |
| #269 | feat: add call-scoped IMAP and SMTP command execution | 2026-10-12 | Not reviewed |  |  |
| #266 | feat: add create_mailbox tool under the organize grant | 2026-10-11 | Adapted | 2d8c31a | Noselect fix; MCP tool not added |
| #265 | fix: decode GB2312-labelled GBK mail and fall back to CJK supersets | 2026-10-06 | Deferred |  | CJK fallback until a real decode failure; crash fixed in a0eeec5 |
| #261 | feat: add per-account mutation permissions, drafts, and Junk workflows | 2026-10-02 | Not reviewed |  |  |
| #260 | fix(ui): claim the Windows loopback listener exclusively | 2026-09-29 | Not reviewed |  |  |
| #258 | fix: defer attachment filesystem access until the request is authorized | 2026-09-29 | Not reviewed |  |  |
| #253 | Fix managed attachment content policy propagation | 2026-09-14 | Not reviewed |  |  |
| #250 | fix: support explicit recipient glob allowlists | 2026-09-07 | Not reviewed |  |  |
| #249 | feat: report the delivered Message-Id from send_email and forward_email | 2026-09-07 | Not reviewed |  |  |
| #248 | fix: deny recipient-bound operations for empty allowlists | 2026-09-07 | Not reviewed |  |  |
| #246 | fix(container): correct OCI license label | 2026-09-04 | Not reviewed |  |  |
| #245 | Restore a container image build and refresh the locked dependencies that carry advisories | 2026-09-04 | Adapted | dba336a | Latest allowed, not upstream pins |
| #244 | fix(metadata): enforce exact datetime filtering | 2026-09-03 | Not reviewed |  |  |
| #242 | feat: add semantic IMAP tags and embedded attachment content | 2026-09-01 | Not reviewed |  |  |
| #239 | Fix bare Message-IDs in reply threading headers | 2026-08-28 | Adapted | 47fdb62 | Description change only |
| #237 | fix(imap): handle omitted empty search responses | 2026-08-26 | Ported | 9dcecb0 | Stricter: any non-numeric line is empty |
| #184 | feat: add forward_email tool | 2026-08-26 | Not reviewed |  |  |
| #234 | fix(smtp): validate DATA transport requirements | 2026-08-23 | Not reviewed |  |  |
| #230 | fix(email): close RFC 5322, SMTPUTF8, MIME, and IMAP interoperability gaps | 2026-08-08 | Adapted | 44ad147, adf831a, a0eeec5, abb94fb, 797e442 | Also 9f502ed LIST; SMTPUTF8 ruled out |
| #229 | fix(smtp): separate header and envelope senders | 2026-08-08 | Ported | e4275fe | With #193 |
| #227 | fix(imap): parse short message literals | 2026-08-05 | Ported | 7a7b85f | |
| #226 | chore(metadata): improve project description | 2026-08-05 | Not reviewed |  |  |
| #224 | feat(attachments): add safe default download directory | 2026-08-04 | Not reviewed |  |  |
| #222 | feat(windows): add first-class runtime support | 2026-08-04 | Not reviewed |  |  |
| #220 | fix(smtp): add redacted phase-level logging | 2026-08-03 | Not reviewed |  |  |
| #219 | fix(email): add provider compatibility headers | 2026-08-03 | Deferred |  | User-Agent headers until a GMX/web.de rejection |
| #218 | Fix RFC 2971 ID compatibility for strict IMAP servers | 2026-08-01 | Ported | d8c0dc7 | |
| #215 | fix(email): preserve IMAP wire and thread headers | 2026-07-30 | Ported | a903839, 775bc62 | Also 7b4a901 (Message-ID unfold) |
| #214 | feat: add bounded email flag mutations | 2026-07-30 | Adapted | f42cf0d, 5499a5b, b237514 | Fixed our flag bugs; V2 tool not adopted |
| #212 | feat: deliver Local Email App V2 with agent-first MCP and CLI contracts | 2026-07-25 | Adapted | 9d32fa0, ffe83a9, aca0d64 | Sub-items only; V2 rewrite not applicable |
| #208 | docs(spec): refine local email app architecture | 2026-07-18 | Not reviewed |  |  |
| #207 | ci: run GreenMail E2E in GitHub Actions | 2026-07-18 | Not reviewed |  |  |
| #206 | chore: add local email app architecture and validation baseline | 2026-07-18 | Not reviewed |  |  |
| #205 | docs: reorganize user documentation | 2026-07-16 | Not reviewed |  |  |
| #204 | chore: add pyright checks and update Python tooling | 2026-07-16 | Not reviewed |  |  |
| #203 | chore: migrate project infrastructure to wh1isper | 2026-07-16 | Not reviewed |  |  |
| #199 | feat: credential security — owner-only config perms + OS keyring storage (combines #195 + #196) | 2026-07-13 | Adapted | 31f740b | 0600 atomic write only; keyring ruled out |
| #198 | fix(ui): don't require SMTP host when adding an email account | 2026-07-11 | Not reviewed |  |  |
| #194 | feat: allow save_to_mailbox without SMTP configuration | 2026-07-11 | Not reviewed |  |  |
| #187 | feat: enforce sender allowlist on mutation tools (follow-up to #180) | 2026-07-07 | Not reviewed | 0fdcddc | STORE side fix ported; allowlist open |
| `32858e3` | feat(tests): add unit tests for installer functionality | 2026-07-06 | Not reviewed |  |  |
| #193 | fix(email): encode only sender display name | 2026-07-05 | Ported | e4275fe | With #229 |
| #182 | feat: add max_body_length to get_emails_content for configurable body truncation | 2026-07-05 | Not reviewed |  |  |
| #190 | fix: declare CHARSET utf-8 for non-ASCII search criteria | 2026-07-05 | Not reviewed |  |  |
| #180 | feat: add global sender allowlist for list_emails_metadata and get_emails_content | 2026-06-26 | Not reviewed |  |  |
| #186 | fix(email): preserve useful HTML link URLs | 2026-06-25 | Not reviewed |  |  |
| #181 | feat: add body / text / has_attachment filters to list_emails_metadata | 2026-06-25 | Not reviewed |  |  |
| #183 | feat: add archive_emails tool | 2026-06-25 | Deferred |  | Archive tool; cheap after 9f502ed |
| #139 | feat: add global recipient allowlist for send_email | 2026-06-24 | Not reviewed |  |  |
| #178 | fix: omit CHARSET in UID SEARCH so Exchange IMAP works | 2026-06-15 | Not reviewed |  |  |
| `0f60b13` | Add funding information for GitHub | 2026-06-03 | Not reviewed |  |  |
| #174 | fix(imap): handle modified UTF-7 mailbox names | 2026-05-29 | Ported | 9f502ed, 4ef5cef | |
| #173 | Add reply_to parameter to send_email | 2026-05-29 | Not reviewed |  |  |
| #171 | feat(config): support read-only email accounts | 2026-05-26 | Not reviewed |  |  |
| #170 | fix(imap): fall back to UID order when dates are missing | 2026-05-26 | Not reviewed |  |  |
| #169 | fix(email): parse HTML fallback with BeautifulSoup | 2026-05-26 | Not reviewed |  |  |
| #168 | fix(attachments): normalize filenames for download matching | 2026-05-26 | Ported | 5384d94 |  |
| #167 | fix(imap): surface SELECT failures before mailbox operations | 2026-05-26 | Ported | cf781f0 | |
| #166 | feat: add IMAP STARTTLS support | 2026-05-26 | Not reviewed |  |  |
| #163 | fix(imap): check LOGIN response.result, raise on failure | 2026-05-26 | Ported | 3552b6c |  |
| #137 | feat: add save_to_mailbox tool for IMAP APPEND to arbitrary folders | 2026-05-26 | Not reviewed |  |  |
| #135 | refactor: Remove get_email_count, derive total from search results | 2026-05-26 | Not reviewed |  |  |
| #149 | feat: add mark_emails_as_read MCP tool | 2026-05-26 | Adapted | fc9be84 | PEEK fetch only; tool covered by flags |
| #147 | feat: add move_emails and list_mailboxes tools | 2026-05-20 | Already covered | 1382757 | Our move/folder tools |
| #160 | fix:inline attachment detection | 2026-05-09 | Adapted | 0d8dd4e | message_id already in 13e8ca0 |
| #159 | fix(cli): allow HTTP transport host configuration | 2026-05-08 | Not reviewed |  |  |
| #155 | fix: sequential chunk fetching + timeout for large mailbox resilience | 2026-05-04 | Not reviewed |  |  |
| #151 | fix: reduce batch fetch chunk size to avoid RecursionError in aioimaplib | 2026-04-07 | Not reviewed |  |  |
| #133 | fix: prevent cleartext credential leak in dispatch_handler error message | 2026-03-18 | Already covered | a6dbd41 | Fork-only client-secret mask added |
| #134 | Fix: Quote multi-word IMAP search criteria values | 2026-03-13 | Not reviewed |  |  |
| — | 15 pre-commit.ci autoupdates, 2026-03-18 to 2026-09-29 | various | Not applicable | | Tooling only |

## Ruled-out directions

Ideas from upstream we decided against, beyond any single PR.

| Idea | Source | Decided | Why | Revisit if |
|---|---|---|---|---|
| Local SQLite metadata cache | #212, #273 | 2026-10-24 | Build caching as a separate client library instead | We need offline or fast local search |
| Batched header fetch with per-batch timeouts | #273 | 2026-10-24 | We fetch one page at a time; aioimaplib times out each command | Large page sizes start hanging |
