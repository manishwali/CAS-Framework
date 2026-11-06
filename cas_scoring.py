"""
cas_scoring.py
CAS (Customer Adoption Signal) scoring: pure functions, no I/O, no framework or app imports --
written so it can be lifted line for line into any backend. The host application loads each
account's per-signal scores and calls these functions; nothing here reads a file or a database.

Two layers:

  Layer 1  SNAPSHOT     A weighted composite of the available signals (0-100), "where is the
                        customer today". A signal with no score for an account is dropped from
                        the denominator and the rest are reweighted in proportion.

  Layer 2  TRAJECTORY   Same-calendar-month year-over-year signals, "which direction is the
                        customer moving". The trajectory score is the equal-weight mean of the
                        available signals.

Tiers (one scale, applied to the snapshot score):
           THRIVING >= 80 | HEALTHY 65-79 | DEVELOPING 50-64 | AT RISK 35-49 | CRITICAL < 35

Headline score = snapshot composite. A composite resting on too little of the weight is not
published (MIN_WEIGHT_COVERAGE), because reweighting two or three signals up to 100% would
present a thin picture as a full one.

This is a generic scoring engine: the signal registries below (SNAPSHOT_SIGNALS,
TRAJECTORY_SIGNALS) are a *template* -- swap in your own signal ids, names, weights and source
descriptions. The math (snapshot composite, reweighting on missing data, trajectory mean,
publish threshold, weakest-signal lookup) is what this module actually contributes.
"""

# id -> (name, weight, source, how it is read)
# Each weight is that signal's % share of the composite; weights sum to SNAPSHOT_TOTAL_WEIGHT.
# This example registry models a B2B SaaS "is this account healthy" composite across usage,
# support, satisfaction, engineering debt, and renewal signals -- replace with your own.
SNAPSHOT_SIGNALS = {
    'S1':  ('REACH',                       17, 'product usage telemetry',        'Distinct users in the last 30 days over active licensed users'),
    'S2':  ('HABIT',                       15, 'product usage telemetry',        "Share of last week's active users who were active again this week, averaged over the last completed month"),
    'S5':  ('SLA compliance + ticket volume', 8, 'support ticketing system',     '0.70 x (100 - (0.60 x resolution-violation % + 0.40 x first-response-violation %)) + 0.30 x MoM ticket-volume score'),
    'S7':  ('CSAT',                         8, 'CRM / health survey',            'Customer health 1-10 and program/service/support ratings, weighted 40/40/20'),
    'S8':  ('Engineering debt',             6, 'issue tracker',                  '100 - (0.50 x impediments + 0.30 x bugs + 0.20 x blocked)'),
    'S9':  ('Power users',                 12, 'product usage telemetry',        'Share of the top-10 users by 12-month event count with 3+ active days in the last 30 days'),
    'S10': ('Escalation risk',              4, 'support ticketing system',       'Open escalations by priority and age'),
    'S11': ('Renewal pipeline',             8, 'CRM opportunity',                'Renewal opportunity probability, stage and amount'),
    'S15': ('Critical-process adoption',   22, 'per-tenant workflow telemetry',  '0.50 x completion % + 0.30 x breadth % + 0.20 x quarter-over-quarter volume score, trailing 4 complete quarters'),
}
MODEL_VERSION = '1.0'
SNAPSHOT_TOTAL_WEIGHT = sum(w for _, w, _, _ in SNAPSHOT_SIGNALS.values())   # 100

# id -> (name, source, how it is scored). All comparisons are same-calendar-month YoY unless noted.
TRAJECTORY_SIGNALS = {
    'T1':  ('Contract value',               'billing system',          '>+10% YoY=100 | -10% to +10%=75 | -25% to <-10%=45 | <-25%=15'),
    'T2':  ('Licensed user count',          'provisioning system',     'Same YoY buckets as T1'),
    'T3':  ('Module breadth',               'product usage telemetry', '+2 or more modules YoY=100 | -1 to +1=75 | -3 to -2=40 | -4 or more=15'),
    'T4':  ('MAU trend',                    'product usage telemetry', '>+15% YoY=100 | -15% to +15%=75 | -30% to <-15%=45 | <-30%=15'),
    'T5':  ('Engagement volume',            'product usage telemetry', 'Same YoY buckets as T4'),
    'T6':  ('Expansion deal velocity',      'CRM opportunity',         'Months since last expansion deal closed: <12mo=100 | 12-24mo=70 | 24-36mo=45 | >3yr or never=20'),
    'T8':  ('DAU/MAU stickiness',           'product usage telemetry', '>40%=100 | 25-40%=80 | 15-25%=60 | 5-15%=40 | <5%=15'),
    'T10': ('Power-user density',           'product usage telemetry', '% of active users with 15+ active days/month: >30%=100 | 15-30%=75 | 8-15%=50 | <8%=25'),
    'T11': ('Config depth (role breadth)',  'provisioning system',     'Snapshot only, no trajectory (no date column): >20 roles=100 | 10-20=75 | 5-10=50 | <5=25'),
    'T12': ('Net revenue retention',        'billing + CRM opportunity', '(prior ARR + expansion - contraction) / prior ARR: >120%=100 | 100-120%=85 | 85-100%=65 | 70-85%=40 | <70%=15'),
    'T13': ('Release cadence',              'delivery tracking',       'Production releases/year: 2+=100 | 1=75 | 1 every 2yr=45 | none in 3+yr=20'),
    'T14': ('Ghost user rate',              'provisioning vs. usage telemetry', '(provisioned active - actual logins 30d) / provisioned active: <20%=100 | 20-40%=75 | 40-60%=50 | 60-80%=30 | >80%=10'),
    'T15': ('Contract term trajectory',     'billing system',          'Term at most recent contract: 5yr=100 | 3yr=80 | 2yr=60 | 1yr=35 | month-to-month=10'),
    'T16': ('Services-to-subscription',     'billing system',          'SUM(services value) / SUM(subscription value), both raw, portfolio in force: <5%=100 | 5-20%=75 | 20-40%=50 | >40%=25'),
    'T17': ('Peak concurrent load',         'product usage telemetry', 'MAX(peak user load)/month YoY; same buckets as T4'),
    'T18': ('Client delivery engagement',   'delivery tracking',       '% of slippage reasons tagged client-caused: 0%=100 | <25%=80 | 25-50%=55 | >50%=25'),
    'T19': ('Executive engagement',         'CRM contact',             '% of Tier 1+2 champions who are active users: >75%=100 | 50-75%=80 | 25-50%=55 | <25%=25 | 0 exec contacts=10'),
    'T20': ('Bug / backlog-item ratio',     'issue tracker',           'Rising ratio = quality perception degrading: <0.5=100 | 0.5-1.0=75 | 1.0-2.0=50 | >2.0=25'),
    'T21': ('Portfolio value trend',        'project management system', 'YoY % change in SUM(project value) over active projects; same buckets as T1'),
    'T22': ('New-project creation rate',    'project management system', 'YoY % change in project count by creation date; same buckets as T1'),
}

# (lower bound, tier), highest first
TIERS = [(80, 'THRIVING'), (65, 'HEALTHY'), (50, 'DEVELOPING'), (35, 'AT RISK'), (0, 'CRITICAL')]

# Minimum share of SNAPSHOT_TOTAL_WEIGHT the available signals must carry before a headline
# score is published.
MIN_WEIGHT_COVERAGE = 0.5

WATCH_BELOW = 60          # a signal scoring under this is called out as a "watch" signal

# Trajectory direction thresholds on the mean trajectory score (mechanical, not a judgement call).
IMPROVING_AT = 75
CONTRACTING_BELOW = 60


def tier_for(score):
    """Tier name for a 0-100 score, or None if there is no score."""
    if score is None:
        return None
    for lower, name in TIERS:
        if score >= lower:
            return name
    return 'CRITICAL'


def _present(scores, valid_ids):
    return {k: float(v) for k, v in (scores or {}).items() if k in valid_ids and v is not None}


def snapshot(scores, weights=None):
    """Layer 1 composite. `scores` maps signal id -> 0-100 or None. `weights` ({id: weight})
    defaults to SNAPSHOT_SIGNALS' weights; pass a different dict to score against a different
    model version.

    Returns {'score': int|None, 'raw': float, 'tier': str|None, 'coverage': 0-1, 'used': [ids],
             'missing': [ids], 'published': bool}. `published` is False when the available weight
    is below MIN_WEIGHT_COVERAGE, in which case score/tier are None."""
    weights = weights if weights is not None else {k: v[1] for k, v in SNAPSHOT_SIGNALS.items()}
    present = _present(scores, weights)
    used_weight = sum(weights[k] for k in present)
    coverage = used_weight / sum(weights.values())
    missing = [k for k in weights if k not in present]
    if not present or coverage < MIN_WEIGHT_COVERAGE:
        return {'score': None, 'raw': None, 'tier': None, 'coverage': round(coverage, 3),
                'used': sorted(present, key=_sid_key), 'missing': missing, 'published': False}
    raw = sum(weights[k] * v for k, v in present.items()) / used_weight
    # ONE canonical precision: the published score is a whole number (round half up) and the tier
    # is derived from that same published value, so the score, the tier and anything that later
    # re-derives a tier from the score can never disagree at a boundary (e.g. 64.6 publishes as
    # 65 / HEALTHY, 64.4 as 64 / DEVELOPING). `raw` keeps the unrounded value.
    score = int(raw + 0.5)
    return {'score': score, 'raw': round(raw, 2), 'tier': tier_for(score), 'coverage': round(coverage, 3),
            'used': sorted(present, key=_sid_key), 'missing': missing, 'published': True}


def trajectory(scores):
    """Layer 2: equal-weight mean of the available T signals.

    Returns {'score': float|None, 'n': int, 'direction': 'Improving'|'Stable'|'Contracting'|None}."""
    present = _present(scores, TRAJECTORY_SIGNALS)
    if not present:
        return {'score': None, 'n': 0, 'direction': None}
    avg = sum(present.values()) / len(present)
    if avg >= IMPROVING_AT:
        direction = 'Improving'
    elif avg < CONTRACTING_BELOW:
        direction = 'Contracting'
    else:
        direction = 'Stable'
    return {'score': round(avg, 1), 'n': len(present), 'direction': direction}


def weakest(scores, n=3, below=WATCH_BELOW):
    """The n lowest-scoring signals under `below`, as [(id, score)], worst first. Works across
    both snapshot and trajectory signal ids."""
    present = {k: v for k, v in _present(scores, {**SNAPSHOT_SIGNALS, **TRAJECTORY_SIGNALS}).items() if v < below}
    return sorted(present.items(), key=lambda kv: (kv[1], _sid_key(kv[0])))[:n]


def overlay_live(baseline, live):
    """Merge live-computed scores over a baseline. A live value that is None never replaces a
    baseline value (a failed or unavailable pull must not blank a known score). Returns
    (merged, sources) where sources maps each present signal id to 'live' or 'baseline'."""
    merged, sources = {}, {}
    for sid, v in (baseline or {}).items():
        if v is not None:
            merged[sid], sources[sid] = v, 'baseline'
    for sid, v in (live or {}).items():
        if v is not None:
            merged[sid], sources[sid] = v, 'live'
    return merged, sources


def evaluate(snapshot_scores, trajectory_scores):
    """Both layers for one account, plus the weakest signals across them."""
    snap = snapshot(snapshot_scores)
    traj = trajectory(trajectory_scores)
    merged = {**(snapshot_scores or {}), **(trajectory_scores or {})}
    return {'snapshot': snap, 'trajectory': traj, 'weakest': weakest(merged),
            'score': snap['score'], 'tier': snap['tier']}


def signal_label(sid):
    """'S9 Power users' style label."""
    meta = SNAPSHOT_SIGNALS.get(sid) or TRAJECTORY_SIGNALS.get(sid)
    return f'{sid} {meta[0]}' if meta else sid


def signal_name(sid):
    """Plain signal name, no internal code (e.g. 'Power users', not 'S9 Power users') -- for
    end-user-facing text where the internal id is noise and only the name and meaning matter."""
    meta = SNAPSHOT_SIGNALS.get(sid) or TRAJECTORY_SIGNALS.get(sid)
    return meta[0] if meta else sid


def signal_description(sid):
    """The registry's own plain-language 'how it is read' sentence for a signal (last tuple
    element for both the 4-tuple S-signals and the 3-tuple T-signals), or None if the id is
    unknown. Used as a guaranteed fallback so end-user-facing text never shows a bare name+score
    with nothing explaining it."""
    meta = SNAPSHOT_SIGNALS.get(sid) or TRAJECTORY_SIGNALS.get(sid)
    return meta[-1] if meta else None


def _sid_key(sid):
    # S before T, then numeric order: S1, S2, ..., S12, T1, ..., T20
    return (0 if sid.startswith('S') else 1, int(sid[1:]))
