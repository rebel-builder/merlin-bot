#!/usr/bin/env python3
"""
Merlin PTZ Easing Library
=========================
Easing functions for smooth, expressive camera movement.

All functions accept t in [0.0, 1.0] and return a value in [0.0, 1.0].
Use interpolate() to apply an easing function to actual position values.

Design intent:
  - linear         → mechanical, robotic
  - ease_in_cubic  → slow start, weight/momentum
  - ease_out_cubic → snap-to-target, quick decel
  - ease_in_out_cubic → natural arc, most human-feeling
  - elastic        → playful overshoot (Merlin being curious)
  - bounce         → bouncy landing (excited/happy)
  - spring         → physically plausible spring (tunable damping)
  - snap           → instant (startle, alert response)

Reference: https://easings.net
"""

import math
import sys


# ── Core easing functions ──────────────────────────────────────────────────


def linear(t: float) -> float:
    """Straight interpolation. No acceleration or deceleration."""
    return float(t)


def ease_in_cubic(t: float) -> float:
    """Slow start, accelerates toward end. Feels like building momentum."""
    return t * t * t


def ease_out_cubic(t: float) -> float:
    """Fast start, decelerates to end. Snaps to target, settles gently."""
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    """Slow start and end, fast middle. Most natural, human-like arc."""
    if t < 0.5:
        return 4.0 * t * t * t
    else:
        return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def elastic(t: float) -> float:
    """Overshoot and oscillate. Use for playful, curious movements.

    Goes slightly past 1.0 then settles — gives Merlin a personality pop.
    Output range: approximately [-0.08, 1.08] (intentional overshoot).
    """
    if t == 0.0:
        return 0.0
    if t == 1.0:
        return 1.0
    c4 = (2.0 * math.pi) / 3.0
    return -(2.0 ** (10.0 * t - 10.0)) * math.sin((t * 10.0 - 10.75) * c4)


def bounce(t: float) -> float:
    """Bouncy landing. Decelerates with multiple bounces at the end.

    Like dropping a ball — quick arrival, energetic settling.
    Output stays in [0.0, 1.0].
    """
    n1 = 7.5625
    d1 = 2.75

    if t < 1.0 / d1:
        return n1 * t * t
    elif t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    elif t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    else:
        t -= 2.625 / d1
        return n1 * t * t + 0.984375


def spring(t: float, damping: float = 0.5) -> float:
    """Spring physics simulation with tunable damping.

    Args:
        t: Progress [0.0, 1.0]
        damping: 0.0 = no damping (max oscillation), 1.0 = critically damped (no overshoot)
                 0.3 = lively spring, 0.5 = natural, 0.8 = gentle

    Output range: may exceed [0.0, 1.0] slightly at low damping values.
    """
    if t == 0.0:
        return 0.0
    if t == 1.0:
        return 1.0

    # Clamp damping to valid range
    damping = max(0.01, min(1.0, damping))

    # Angular frequency — controls speed of oscillation
    omega = 2.0 * math.pi * (1.0 + (1.0 - damping) * 2.0)

    # Exponential decay envelope
    decay = math.exp(-damping * omega * t)

    # Oscillation
    if damping < 1.0:
        omega_d = omega * math.sqrt(max(0.0, 1.0 - damping ** 2))
        oscillation = math.cos(omega_d * t) + (damping * omega / omega_d) * math.sin(omega_d * t)
    else:
        # Critically damped — no oscillation
        oscillation = 1.0 + omega * t

    return 1.0 - decay * oscillation


def snap(t: float) -> float:
    """Instant movement. Zero travel time, arrives immediately.

    Returns 0.0 for all t < 1.0, then 1.0 at completion.
    Use for startle responses and immediate reactions.
    """
    return 1.0 if t >= 1.0 else 0.0


# ── Position interpolation ─────────────────────────────────────────────────


def interpolate(start: float, end: float, t: float, easing_fn=ease_in_out_cubic, **kwargs) -> float:
    """Apply an easing function to actual position values.

    Args:
        start: Starting position (e.g., current pan angle in degrees)
        end: Target position
        t: Progress [0.0, 1.0]
        easing_fn: Any easing function from this module
        **kwargs: Extra arguments forwarded to easing_fn (e.g., damping for spring)

    Returns:
        Interpolated position at time t

    Example:
        # Smooth 2-second pan from 0° to 45°
        for step in range(100):
            t = step / 99.0
            pos = interpolate(0, 45, t, ease_out_cubic)
            send_ptz_pan(pos)

        # Spring-based move with custom damping
        pos = interpolate(current_pan, target_pan, t, spring, damping=0.3)
    """
    eased = easing_fn(t, **kwargs) if kwargs else easing_fn(t)
    return start + (end - start) * eased


# ── Registry ───────────────────────────────────────────────────────────────

# Named lookup for runtime selection (e.g., from gesture definitions)
EASING_FUNCTIONS = {
    "linear":           linear,
    "ease_in_cubic":    ease_in_cubic,
    "ease_out_cubic":   ease_out_cubic,
    "ease_in_out_cubic": ease_in_out_cubic,
    "elastic":          elastic,
    "bounce":           bounce,
    "spring":           spring,
    "snap":             snap,
}


def get_easing(name: str):
    """Retrieve an easing function by name string.

    Args:
        name: One of the keys in EASING_FUNCTIONS

    Returns:
        The easing function, or ease_in_out_cubic as default.
    """
    return EASING_FUNCTIONS.get(name, ease_in_out_cubic)


# ── ASCII visualization (demo mode) ───────────────────────────────────────


def _visualize(name: str, fn, steps: int = 40, height: int = 10, **kwargs):
    """Print an ASCII curve for a single easing function."""
    # Sample the function
    samples = []
    for i in range(steps + 1):
        t = i / steps
        try:
            val = fn(t, **kwargs) if kwargs else fn(t)
        except Exception:
            val = 0.0
        samples.append(val)

    # Find actual min/max (some functions like elastic go outside 0-1)
    lo = min(samples)
    hi = max(samples)
    span = hi - lo if hi != lo else 1.0

    # Build grid: rows (height) x cols (steps+1)
    grid = [[" "] * (steps + 1) for _ in range(height)]
    for col, val in enumerate(samples):
        # Normalize to grid row (row 0 = top = high value)
        normalized = (val - lo) / span
        row = int(round((1.0 - normalized) * (height - 1)))
        row = max(0, min(height - 1, row))
        grid[row][col] = "█"

    # Print
    label = f"  {name}"
    if kwargs:
        label += " (" + ", ".join(f"{k}={v}" for k, v in kwargs.items()) + ")"
    print(label)

    # Y-axis labels
    hi_label = f"{hi:+.2f}"
    lo_label = f"{lo:+.2f}"

    for r, row in enumerate(grid):
        if r == 0:
            prefix = f"{hi_label} │"
        elif r == height - 1:
            prefix = f"{lo_label} │"
        else:
            prefix = "       │"
        print(prefix + "".join(row))

    # X-axis
    print("       └" + "─" * (steps + 1))
    print("        0" + " " * (steps - 8) + "0.5" + " " * (steps // 2 - 4) + "1.0")
    print()


def demo():
    """Print ASCII visualizations of all easing curves."""
    print()
    print("=" * 60)
    print("  Merlin PTZ Easing Library — Curve Visualizer")
    print("=" * 60)
    print()

    _visualize("linear", linear)
    _visualize("ease_in_cubic", ease_in_cubic)
    _visualize("ease_out_cubic", ease_out_cubic)
    _visualize("ease_in_out_cubic", ease_in_out_cubic)
    _visualize("elastic", elastic)
    _visualize("bounce", bounce)
    _visualize("spring (damping=0.3)", spring, damping=0.3)
    _visualize("spring (damping=0.5)", spring, damping=0.5)
    _visualize("spring (damping=0.8)", spring, damping=0.8)
    _visualize("snap", snap)

    print("=" * 60)
    print()
    print("  interpolate() example — pan 0° → 45° with ease_out_cubic:")
    print()
    positions = []
    for i in range(11):
        t = i / 10.0
        pos = interpolate(0.0, 45.0, t, ease_out_cubic)
        positions.append(pos)
        bar = "█" * int(pos / 2)
        print(f"    t={t:.1f}  pos={pos:6.2f}°  {bar}")
    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    demo()
