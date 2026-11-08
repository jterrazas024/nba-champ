# Data source research (programmatic NBA data for the daily pipeline)

Checked on 2026-11-07. Documentation-only research: no accounts created, no keys requested, no requests sent to Basketball-Reference, stats.nba.com or cdn.nba.com. Pricing was read from provider pages where they loaded; where a page returned 403 to our fetcher (API-Sports) I used search snippets and secondary write-ups and marked the claims UNVERIFIED. We did not try to get around any block.

Requirement: an official or intended-for-programmatic API with a free or cheap key, terms that allow a public non-commercial hobby site, and access from GitHub Actions once a day. Keys would live in GitHub Actions secrets.

Data items (model weight): 1 team net rating / SRS / ORtg / DRtg (38%); 2 player-impact metric, BPM or alternative (25%); 3 player minutes and games played, current + prior two seasons (15%); 4 last-20 results with scores (10%); 5 injuries; 6 clutch splits (2%); 7 full schedule and final scores.

## Comparison table

Legend: Y = documented, P = partial / derivable by us, N = no, ? = UNVERIFIED.

| Candidate | 1 Team rating | 2 Player impact (BPM?) | 3 Minutes / GP, 3 seasons | 4 Last 20 | 5 Injuries | 6 Clutch | 7 Schedule / scores | Price | Terms fit (public hobby site, daily cloud use) | Key |
|---|---|---|---|---|---|---|---|---|---|---|
| balldontlie Free | P (compute from scores) | N | N (no player stats) | Y (games) | N | N | Y (`/games`, history 1946-current claimed) | $0, 5 req/min | Y, ToS allows personal or commercial use | Yes |
| balldontlie ALL-STAR (NBA) | P (compute) | P (box-score components, we could build our own) | Y (game player stats, per-game minutes) | Y | Y (`/player_injuries`) | P (proxy from scores) | Y | $9.99/mo, 60 req/min | Y | Yes |
| balldontlie GOAT (NBA) | Y (game-level `net_rating`, `offensive_rating`, `defensive_rating`; team season averages `general/advanced`; standings) | N for BPM; PIE, usage, net_rating per player; no BPM/EPM/VORP documented | Y (season averages from 1996) | Y | Y | Y (`clutch` categories for team and player season averages) | Y | $39.99/mo, 600 req/min | Y | Yes |
| API-Sports (API-NBA) | ? (standings and team stats exist; SRS / net rating not documented here) | ? | ? | P (games) | ? | N | Y | Free 100 req/day; Pro $15/mo (7,500/day) | ? Terms not read (docs 403) | Yes |
| SportsDataIO (commercial) | P (TeamSeasonStats totals, Standings) | ? (PER/usage not listed) | Y (PlayerSeasonStats) | Y (Games) | Y (`InjuredPlayers`) | ? | Y | Quote only; third-party estimates $500+/mo (UNVERIFIED) | N for hobby on commercial feed. Free Discovery Lab is personal/non-commercial only, public-facing products need a commercial licence | Yes |
| SportsDataIO Discovery Lab | ? | ? | ? | ? | ? | ? | Free access to LAST season only | Free (last season); paid personal packages, prices UNVERIFIED | N for a public site: "public-facing ... require a commercial license" | Yes |
| Sportradar | ? | ? | ? | ? | ? | ? | ? | Trial only: 30 days, 1,000 calls / 30 days, 1 QPS; production by contract | N (B2B contract; trial expires) | Yes |
| MySportsFeeds | ? | ? | ? | ? | ? | ? | ? | UNVERIFIED: personal plans reported from about CAD 5/mo, base about CAD 29/mo | ? Official pricing page not reachable (404) | Yes |
| ESPN unofficial JSON | P (standings, scoreboard) | N | P | Y (scoreboard by date) | ? | N | Y | Free | Unclear/no: undocumented, no developer terms; Disney ToS bars automated access and commercial use | No |
| `nba_api` package | Y (but source is stats.nba.com) | N (no BPM; has PIE etc.) | Y | Y | N | Y | Y | Free | Library is MIT, data is NBA's; cloud IPs are blocked | No |
| nbadb (Kaggle / GitHub, built from stats.nba.com) | Y (team stats, advanced, clutch, standings per README) | N (BPM not listed) | Y | Y | Not listed | Y (clutch stats) | Y | Free | MIT code; underlying data from stats.nba.com (NBA terms apply) | No |
| hoopR / sportsdataverse (ESPN-sourced, GitHub releases) | P | N | P (player box scores) | Y | N | N | Y (schedule, team and player box scores) | Free | Repo licence MIT-style; data is ESPN's (ESPN terms apply). Cadence unverified for now | No |
| Sports-Reference / Stathead | Y | Y (BPM, SRS) | Y | Y | N | P | Y | No API ("not the business model") | Scraping to build sites/tools needs permission | n/a |

## Per-candidate notes

### balldontlie (best fit found)
Sources: https://www.balldontlie.io/#pricing , https://docs.balldontlie.io/ , https://www.balldontlie.io/terms (all fetched 2026-11-07).

- Plans (official pricing page): Free $0, 5 req/min; All-Star $9.99/mo per league, 60 req/min; GOAT $39.99/mo per league, 600 req/min; All-Access $299.99/mo (a secondary write-up says $499.99; the official page is more current, but note the discrepancy). Paid plans have a 7-day free trial in the dashboard. Prices are per league, so NBA only is $9.99 or $39.99. Confidence: high (official page).
- Tier gating per the docs: Free = Teams, Players, Games. All-Star adds Game Player Stats, Active Players, Player Injuries. GOAT adds Season Averages, Team Season Averages, Game Advanced Stats (v1, v2), Box Scores, Lineups, Standings, Leaders, Betting Odds, Plays, and more. Confidence: high.
- Item 7 (schedule and scores): `/games` has `home_team_score`, `visitor_team_score`, `season`, `postseason`, `status`, `datetime`; filter by `seasons[]`, `season_type`. Free tier. Quarter scores only from 2023 on.
- Item 1: no SRS endpoint. Game Advanced Stats (GOAT) carries `offensive_rating`, `defensive_rating`, `net_rating`, `pace`, `possessions` (v2 from 2015), but these are per-player-per-game on-court numbers. Team Season Averages (`/nba/v1/team_season_averages/{category}`, from 1996) has `general` types base/advanced/opponent/defense; the docs I could read list `plus_minus` in the base sample but I could not confirm whether `advanced` exposes team net rating (UNVERIFIED). Safest plan: compute net rating and SRS ourselves from final scores (free tier).
- Item 2: BPM is not offered. GOAT offers `pie` (Player Impact Estimate), `usage_percentage`, per-player `net_rating`. PER, EPM, RAPTOR, VORP not offered. Options only: PIE as a stand-in, or compute an approximate BPM from game player stats (the BPM formula uses box-score components; an owner decision is needed before any change).
- Item 3: Game Player Stats (All-Star) gives per-game minutes, so minutes and GP for three seasons can be summed, but that is many paged requests (60 req/min, `per_page` max 100). GOAT `season_averages` (from 1996) gives it per season in far fewer calls.
- Item 5: `/v1/player_injuries` (All-Star): `player`, `status` (e.g. "Out"), `return_date`, `description`. Current only, no history.
- Item 6: GOAT has `clutch` categories (base, advanced, misc, scoring) for team season averages, but exact field names were not shown (UNVERIFIED). On cheaper tiers use a close-game proxy from scores.
- Terms (https://www.balldontlie.io/terms): Section 6 allows use of data "for any lawful personal or commercial purpose"; no attribution required; no distinction between commercial and non-commercial; cannot build a competing data API from it; cannot evade rate limits (7(c)); automated access must go through official interfaces (7(d)), which is exactly what an API key is. Daily automated use from GitHub Actions is consistent with this. Confidence: high on the summary, but I read it through a summarizing fetch, so the owner should read Sections 6 and 7 once.
- Key: required (header). Freshness: not documented; UNVERIFIED how quickly final scores post.

### API-Sports (API-NBA)
Sources: https://api-sports.io/sports/nba (search snippet only), https://www.api-football.com/news/post/how-to-get-started-with-api-nba-the-complete-beginners-guide (403), RapidAPI listing, https://highlightly.net/blogs/best-nba-apis-in-2026 . api-sports.io docs and pricing pages returned 403 to our fetcher, so most claims are UNVERIFIED.
- Free 100 requests/day (several sources agree); Pro $15/mo, 7,500/day; Ultra $25/mo; Mega $35/mo; prepaid, no overage (secondary source). Features reportedly not gated by plan.
- Likely endpoints: games, standings, teams, team statistics, player statistics. Net rating / SRS, injuries, clutch not confirmed. Terms of service not read.
- Cheaper than balldontlie GOAT but I cannot confirm it covers more of items 1-6. Rate it a candidate to verify, not a recommendation.

### SportsDataIO
Sources: https://sportsdata.io/developers/api-documentation/nba , https://sportsdata.io/help/data-rights-and-licensing-questions .
- Endpoints exist for Standings, TeamSeasonStats, PlayerSeasonStats, `InjuredPlayers`, Games by season/date. No PER/BPM listed.
- Free trial documented as UEFA Champions League only (NBA via Replay or sales). Commercial licences are quoted per client.
- Discovery Lab (personal use): free access to last season's data only; "public-facing, revenue-generating, or business-operated products require a commercial license". A public hobby website therefore does not fit. Not recommended.

### Sportradar
Sources: https://developer.sportradar.com/getting-started/docs/your-account (via search).
- 30-day trial, 1,000 calls per rolling 30 days, 1 QPS; production for contracted customers. Trial terms on non-commercial use not found (UNVERIFIED). Not suitable for a daily pipeline beyond 30 days.

### MySportsFeeds
Sources: search results only; the pricing URL I tried returned 404. A wrapper README says v1.x feeds were free for non-commercial use and v2.0 asks for a donation; a blog says personal plans from CAD 5/mo and base CAD 29/mo. All UNVERIFIED and possibly stale. Worth one manual check of mysportsfeeds.com by the owner.

### ESPN unofficial JSON endpoints
Sources: https://github.com/pseudo-r/Public-ESPN-API , https://zuplo.com/learning-center/espn-hidden-api-guide , https://disneytermsofuse.com/english/ .
- Undocumented, unsupported, can change without notice. No developer terms exist for them. The Disney terms prohibit accessing products "using a robot, spider, script, or other automated means" and any commercial use; the license is "personal, noncommercial use only". Honest status: tolerated in practice by the community, not licensed. Does not meet the "meant for programmatic access" requirement. Not recommended.

### nba_api package
Sources: https://github.com/swar/nba_api (README as relayed via search), https://github.com/swar/nba_api/issues/153 , Stack Overflow thread on GitHub workflow timeouts.
- It is a client for stats.nba.com. The README says stats.nba.com does not respond to many cloud IP ranges (AWS, GCP, Azure, etc.); reported fix is a proxy (`NBA_API_PROXY`). Our laptop timeouts and GitHub Actions timeouts are the same blocking. Using a proxy to get around this would be circumventing protection, which we are not doing. The `live` endpoints are on cdn.nba.com, which returned 403 for us. Not usable.

### Open datasets and mirrors
- nbadb (https://github.com/wyattowalsh/nbadb , Kaggle `wyattowalsh/basketball`): MIT; daily refresh of the current season via a pipeline; built from stats.nba.com through nba_api; includes team game logs, standings, clutch, advanced box scores. Concerns: it is a person's pipeline run on their own schedule (freshness UNVERIFIED, page shows no last-updated date), and the data's underlying terms are NBA's. Could be a read-only fallback, not a primary source.
- hoopR-nba-data (https://github.com/sportsdataverse/hoopR-nba-data): ESPN-sourced schedules, team and player box scores, standings published as GitHub release assets. Workflow cron 07:00 UTC; fetched page showed last data publish 2026-08-26 and a seasonal schedule (daily Nov-Jun). Cadence in the live season UNVERIFIED. Licence file present, type not read. Data originates at ESPN, so ESPN terms are a question.
- Kaggle NBA datasets (e.g. NBA All Time Stats, NBA Play-by-Play and Box Scores) claim daily updates but licences were not confirmed and several derive from Basketball-Reference or stats.nba.com. UNVERIFIED.

### Basketball-Reference / Sports-Reference (for context)
Sources: https://www.sports-reference.com/data_use.html (403 to us; read via search snippets), https://aws.sports-reference.com/bot-traffic.html .
- Their policy says not to build websites or tools from scraped data without permission; Stathead and FBref blocked above 10 requests/min; "An API is not the business model." So BPM/SRS from there is not obtainable under a clean programmatic licence. NatStat was suggested by them as a low-cost data option; its current pricing and terms were not verifiable (archived 2022 page: from $8.99/mo, 500 calls/hour, UNVERIFIED).

## Gaps that remain with any option found

- BPM: no provider documents BPM. Choices (owner decision): keep BPM by computing an approximation from box-score components, or switch to a provider-supplied alternative (PIE from balldontlie GOAT). Do not switch without approval.
- SRS: compute ourselves from scores (simple iterative solve), with ORtg/DRtg approximated from points per estimated possessions or taken from advanced stats.
- Clutch: only on GOAT (field names UNVERIFIED); otherwise use a close-game proxy from scores.
- Injury history is not available; only current status.

## Recommendation

balldontlie. Free tier already covers items 7 and 4 (full schedule and final scores, so we can compute net rating, SRS, last-20 and a close-game proxy). ALL-STAR at $9.99/mo adds current injuries and per-game player minutes (items 3, 5). GOAT at $39.99/mo adds season averages from 1996, advanced stats (PIE, usage), team season averages, and clutch categories. Terms explicitly allow personal or commercial use with no attribution. Needs the owner to approve any paid tier and to pick the star metric (BPM computed in-house vs PIE).

---

## Community research (checked 2026-11-07)

Scope: what hobbyists and developers report doing since late 2025 to feed daily NBA dashboards and models from cloud servers or GitHub Actions. Method: WebSearch plus WebFetch on specific threads; research only, nothing probed or circumvented.

**Important limitation: no Reddit thread surfaced.** Searches aimed at r/nba, r/algobetting, r/datasets, r/learnpython, r/dataengineering and r/sportsbook returned zero Reddit results (UNVERIFIED whether relevant threads exist; I did not try to fetch Reddit directly). Stack Overflow also gave nothing newer than the 2022 thread already cited. The evidence below is therefore mostly **GitHub issues and pull requests by individual developers, one Hacker News post, and vendor/blog write-ups**. Each is an anecdote. Many are small personal repos, so weigh them as "someone hit this and did X", not as consensus. None showed obvious affiliate links; several vendor comparison pages (bigballsdata.com, freeapi.watch, highlightly.net) are promotional and I did not rely on them.

### What the evidence says about access from GitHub Actions / cloud

| Finding | Source and date | Corroboration |
|---|---|---|
| cdn.nba.com returned HTTP 403 for every request from GitHub runners (schedule, scoreboard, box scores), regardless of headers, including nba_api's header set. The project switched to ESPN's scoreboard (one day per request; date ranges returned 400) and game-summary endpoints, which worked from the runner. | https://github.com/vladk-III/NBA/pull/2 (opened and merged 2026-10-15) | A second project's open issue lists the same blocker, "Recover actual NBA-operated original schedule/final endpoints from GitHub Actions", still open 2026-11-06: https://github.com/frederickwalker66-cmyk/Freddy-s-sports-data/issues/41 |
| stats.nba.com timeouts for the 2026-27 season on a Render cron job and on a local Windows laptop; headers, library upgrade and plain `requests` did not help. The maintainer said the same script works for him and suggested a network issue. Issue open. | https://github.com/swar/nba_api/issues/728 (2026-10-22/23) | Earlier timeouts on a second request in one session, https://github.com/swar/nba_api/issues/633 ; Go client author reports a 2026-07-22 sweep where only 5 of 141 stats endpoints responded from every network tried including Actions runners (via search snippet of https://github.com/n-ae/nba-api-go , UNVERIFIED, not opened). |
| Show HN author says stats.nba.com is behind Akamai TLS-fingerprint checks and drops datacenter IPs, and that cdn.nba.com live endpoints work from anywhere. His suggested remedy is TLS impersonation tools. We will not do that. | https://news.ycombinator.com/item?id=47052117 (about 8 months old; no third-party comments) | His cdn.nba.com claim is now contradicted by the two October 2026 reports above. Treat my earlier mention that cdn.nba.com is reachable "from anywhere" as outdated. |
| A fallback PR says stats.nba.com often refuses cloud IPs, and the first scheduled import failed on Actions; it added a cdn.nba.com fallback. | Search snippet of https://github.com/dduffield84/nba-first-basket-api/pull/4 (page itself returned 404 when fetched; UNVERIFIED) | Conflicts with the 403 reports on cdn.nba.com; the date is unknown. |

Convergence: stats.nba.com is unreliable from any cloud IP, and as of October 2026 cdn.nba.com is also reported as 403 from GitHub Actions. Developers who need a working scheduled job have moved to ESPN's undocumented endpoints or a paid/keyed vendor. Basketball-Reference works only at a very slow pace and gets "jailed" for up to a day on any 403/429.

### Basketball-Reference reports
- https://github.com/Hredo/globalhoopstats/pull/70 (2026-10-23): fetcher sent about 50 req/min and retried 403s, which kept the block alive; now waits 3.5 s between requests (about 17/min), does not retry 4xx, and degrades gracefully. Says BR allows about 20/min.
- https://github.com/yusufaf/nba-central/pull/258 (2026-10-24/25): a scheduled Lambda scrapes BR at 3.5 s spacing, pauses 24 h on any 429/403 or blank page ("jailed for up to a day"), initial backfill about 9 hours, then about 1,000 requests a week. Where it runs from (and whether BR is reachable from that cloud IP) is not stated.
- These show people are still scraping BR politely, but they do not show that it works from GitHub Actions IPs. Our own laptop got a Cloudflare 403. Sports-Reference's own page says building tools on scraped data needs permission (see earlier section). No one in these sources reported a licensed alternative for BPM/SRS.

### Answers to the four questions

**(1) Free, daily BPM or equivalent?** No source found supplies BPM free and daily in bulk.
- BPM: only at Basketball-Reference (scraping, permission concerns) per https://nbaanalytic.com/articles/free-basketball-data-sources-ranked.html (published 2026-06-04, updated 2026-09-25; no affiliate links evident; says nothing about cloud use).
- EPM: Dunks & Threes publishes leaderboards; the awesome-nba-data catalog (https://github.com/JovaniPink/awesome-nba-data , reviewed Aug 2026) lists them as "free leaderboards", and NBAAnalytic describes them as "partly gated". No download or API was documented anywhere I read (UNVERIFIED). Not a pipeline source.
- RAPTOR: stopped in 2023 (https://nbarapm.com/datasets/nba). Archived FiveThirtyEight data is historical only.
- RAPM-family and hybrid metrics (DARKO DPM, LEBRON, MAMBA, LAKER) are displayed at https://nbarapm.com/datasets/nba , which lists "Full Datasets" but states no licence, cadence, or API (UNVERIFIED; run by "databallr", data credited to other creators). Using it would be a new scrape of a new site and needs the owner's approval first.
- Realistic path (consistent with my earlier note): compute a BPM approximation from box-score data ourselves, or pick a different metric (owner decision).

**(2) Free keyed APIs or maintained mirrors reported to work from GitHub Actions?**
- ESPN scoreboard and summary endpoints: reported working from a runner (vladk-III/NBA#2, 2026-10-15). Coverage: schedule and scores (item 7), team box scores. One source says another project rejected ESPN for the Disney terms; that project is not identified (UNVERIFIED). ESPN has no developer terms for these endpoints; see the earlier ESPN section for the Disney clause on automated access. Community use does not make it licensed.
- New candidate: **BoxScore Lab** (https://boxscorelab.com/downloads/, fetched 2026-11-07). One-person site; 15 free CSVs for 2022-23 to 2026-27: player season totals (32 columns, including games, minutes, usage, true shooting), team standings and ratings (record, points for/against, estimated ORtg/DRtg, **net rating**, **strength of schedule**, pace), and team game results (2,460 rows per season, each game once per team, with scores). Rebuilt nightly; the copy I saw was build 20261107T1218 with data through 2026-11-07. CC BY 4.0 (commercial use allowed with attribution), no key, no rate limit, no ads or paywall (voluntary Stripe donations). Covers items 1 (net rating, SoS), 3 (minutes, GP, for 4 seasons), 4 and 7 (game results with scores). Does **not** cover BPM, injuries, or clutch. Caveats: only its own page vouches for it (zero independent mentions found), single maintainer, ratings are labeled "estimated", and the page says the data source is NBA Stats, so upstream NBA terms are unclear. Because it is a static file download on a normal website, no bot defense should be involved, but I did not test from Actions (and I must not).
- nbadb on Kaggle/GitHub and hoopR-nba-data releases (see earlier section): no community confirmation found that either runs reliably on a schedule in 2026.
- balldontlie: see next point.
- "Bet Better" free CSV/JSON of model outputs (CC BY 4.0) appears in awesome-nba-data; those are someone else's predictions, not inputs, so not appropriate for this project.

**(3) What people say about balldontlie.** Very little. No Reddit/HN thread found.
- Free tier needs an API key and is 5 req/min (https://freeapi.watch/free-sports-apis/ , 2026; matches the official pricing page). Older pages (SourceForge, publicapis) still say 60/min and no key; stale.
- One third-party health check measured about 671 ms average response (public-apis, April 2026); one project note says responses took 10-30 s on the GOAT tier (via search snippet, single source, UNVERIFIED which project).
- A search summary said one project chose balldontlie after rejecting NBA.com, BR and ESPN for terms reasons, but I could not tie it to a specific, openable source (UNVERIFIED).
- No source I read reported data accuracy errors or freshness lag. So uptime, freshness and accuracy remain unknown; test during the 7-day free trial before paying.

**(4) Warnings about sources I ruled out or recommended.**
- nba_api: still maintained (changelog activity Aug 2026) but cloud timeouts are open and reproduced (issue #728). Confirms the earlier "not usable from Actions".
- cdn.nba.com: now reported as 403 from Actions (two October 2026 projects). Do not plan around it.
- ESPN: works today, undocumented, can change without notice; the Disney ToS still forbids scripted access.
- Basketball-Reference: pacing at about 17/min is the community norm; a single 403/429 means a one-day pause; retrying makes it worse.
- Sports-Reference/Stathead: remains no-API.
- balldontlie: no warnings found, but also almost no independent validation.

### Effect on the earlier recommendation
- balldontlie stays the primary recommendation for items 4-7 (nothing contradicts it, and every other keyless route is reported blocked or ToS-grey), with the caveat that community validation is thin.
- Change: drop any assumption that cdn.nba.com is a safe fallback.
- New option worth owner consideration: BoxScore Lab for net rating / SoS / minutes / game results at zero cost and zero keys. It could replace the need for computing SRS ourselves and could let us stay on balldontlie's free tier or ALL-STAR (injuries only). Risks: single maintainer, unverified upstream licence, no independent corroboration. Suggest using it as a cross-check or secondary source, not the sole one, and asking the owner before adding a new site to the pipeline (CLAUDE.md requires approval for new scrape targets, though this is a published download).
- BPM remains uncovered by every free daily source.

### Inaccessible or UNVERIFIED in this pass
- Reddit (no results from search; direct fetch not attempted), Stack Overflow (no recent hits), Kaggle pages, r/algobetting content.
- https://github.com/dduffield84/nba-first-basket-api/pull/4 returned 404.
- HN thread has only the author's own comment.
- Dunks & Threes data access, nbarapm.com licence and cadence, BoxScore Lab upstream terms, balldontlie freshness.
