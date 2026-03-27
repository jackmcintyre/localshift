from __future__ import annotations

from dataclasses import dataclass, field

from ..const import CHARGE_RATE_MAX_KW, CHARGE_RATE_MIN_SAMPLES


@dataclass
class ChargeRateCurve:
    """Charge rate curve model keyed by SOC percent."""

    bins: dict[int, float]
    sample_count: int
    normalized_mad: float
    min_samples: int
    _sorted_bins: tuple[tuple[int, float], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._sorted_bins = tuple(sorted(self.bins.items()))

    @classmethod
    def from_bins(
        cls,
        bins: dict[int, float],
        sample_count: int = 0,
        normalized_mad: float = 0.0,
        min_samples: int = CHARGE_RATE_MIN_SAMPLES,
    ) -> ChargeRateCurve:
        """Create a curve from SOC bins and sample metadata."""
        return cls(
            bins=bins,
            sample_count=sample_count,
            normalized_mad=normalized_mad,
            min_samples=min_samples,
        )

    def rate_at_soc(self, soc_pct: float) -> float:
        """Return interpolated charge rate at SOC percentage."""
        if not self._sorted_bins:
            return 0.0

        clamped_soc = max(0.0, min(100.0, soc_pct))
        sorted_bins = self._sorted_bins
        min_soc, min_rate = sorted_bins[0]
        max_soc, max_rate = sorted_bins[-1]

        if clamped_soc <= min_soc:
            rate = float(min_rate)
        elif clamped_soc >= max_soc:
            rate = float(max_rate)
        else:
            rate = float(min_rate)
            for (lower_soc, lower_rate), (upper_soc, upper_rate) in zip(
                sorted_bins, sorted_bins[1:], strict=False
            ):
                if lower_soc <= clamped_soc <= upper_soc:
                    span = upper_soc - lower_soc
                    if span == 0:
                        rate = float(lower_rate)
                    else:
                        fraction = (clamped_soc - lower_soc) / span
                        rate = float(lower_rate) + fraction * (
                            float(upper_rate) - float(lower_rate)
                        )
                    break

        return max(0.0, min(rate, CHARGE_RATE_MAX_KW))

    @property
    def confidence(self) -> float:
        """Return confidence score based on samples and dispersion."""
        if self.min_samples <= 0:
            base = 1.0
        else:
            base = min(1.0, self.sample_count / self.min_samples)
        confidence = base * max(0.0, 1.0 - self.normalized_mad)
        return max(0.0, min(1.0, confidence))
