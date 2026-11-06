"""
schemas.py
Pydantic response models for exposing CAS scores over an API. Pair with cas_scoring.py:
these are the shapes you'd return from your endpoints after calling snapshot()/trajectory()/
evaluate(), including the display/partial/coverage conventions that keep a frontend from having
to re-derive any of that logic itself.

Conventions
  * A value the backend does not hold is null, never 0 (unknown date -> null, not a sentinel).
  * A hand/manual estimate is flagged explicitly (CasInfo.source == 'estimate' / is_estimate ==
    true), and the display text already carries an 'est.' suffix -- the frontend renders
    cas_display as-is rather than re-deriving a label from the raw score.
  * Month / as-of / source labels are computed server-side; the frontend only renders them.
"""
from typing import Literal

from pydantic import BaseModel, Field

CasSource = Literal['v1', 'estimate']
SignalSource = Literal['live', 'baseline', 'estimate']


PARTIAL_DOC = ('True for a measured score that rests on under 75% of the signal weight. It is a '
               'real score, not a hand estimate (is_estimate stays false), but it is not comparable '
               'one-for-one with fully covered accounts. Always false for a hand estimate.')
COVERAGE_DOC = ('Fraction of the snapshot signal weight behind the score, as a FRACTION from 0 to 1 '
                '(0.618 = 61.8%), never a percent. null for a hand estimate or when unknown.')
DISPLAY_DOC = ("The score display text: '78', '62 partial' or "
               "'55 est.' (hand estimate). Render this; do not re-derive it. null when there is no score.")


class SignalRef(BaseModel):
    id: str
    label: str


class SnapshotInfo(BaseModel):
    score: int | None
    raw: float | None
    tier: str | None
    coverage: float
    published: bool
    used: list[str]
    missing: list[SignalRef]


class TrajectoryInfo(BaseModel):
    score: float | None
    n: int
    direction: Literal['Improving', 'Stable', 'Contracting'] | None
    line: str | None = Field(description="e.g. 'Trajectory 82.2 ↑ Improving'")


class WeakSignal(BaseModel):
    id: str
    label: str
    score: float


class CasInfo(BaseModel):
    score: int | None
    text: str | None = Field(description="Legacy text: the score with an ' est.' suffix for a hand estimate. It omits the ' partial' marker, so do not display it; use cas_display.")
    tier: str | None
    tier_line: str = Field(description="Tier, plus ' · estimate' for a hand estimate")
    source: CasSource
    is_estimate: bool
    cas_display: str | None = Field(description=DISPLAY_DOC)
    cas_partial: bool = Field(description=PARTIAL_DOC)
    cas_coverage: float | None = Field(description=COVERAGE_DOC)
    partial: bool = Field(description='Snapshot-coverage block flag; same value as cas_partial.')
    missing_signals: list[SignalRef] = Field(
        description='Snapshot signals NOT used in the score (id + label); empty for a hand estimate.')
    snapshot: SnapshotInfo | None
    trajectory: TrajectoryInfo
    weakest: list[WeakSignal]
    breakdown_title: str = Field(description='Heading of the signal breakdown section')
    breakdown_note: str = Field(description='Caption / info text shown above the signal tables')
    trajectory_note: str | None
    missing_note: str | None
    as_of: str | None = Field(description="Date the score's inputs are current as of.")


class SignalRow(BaseModel):
    id: str
    name: str
    layer: Literal['S', 'T']
    weight: int | None = Field(description='Snapshot weight; null for trajectory signals')
    source_system: str | None
    source: SignalSource | None = Field(description='null when the signal is unavailable')
    score: float | None = Field(description='0-100; null = unavailable, never 0')
    watch: bool
    evidence: str | None
    description: str = Field(description='Plain-language explanation of how this signal is read '
                              '(e.g. "Distinct users in the last 30 days over active licensed users") '
                              '-- the signal registry\'s own methodology text, so a reader never '
                              'has to decode a bare code like "S1" or "T20" alone.')
