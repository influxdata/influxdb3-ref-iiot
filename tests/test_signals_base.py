"""Tests for signal primitives. These are the same tests bess uses."""

from __future__ import annotations

import math

import pytest

from simulator.signals_base import burst, jitter, random_walk, sinusoid, step


def test_sinusoid_zero_phase_at_t_zero():
    # sin(0) = 0, so sinusoid(0, ...) = offset + 0 = offset
    assert sinusoid(0.0, period_s=10.0, amplitude=2.0, offset=5.0) == pytest.approx(5.0)


def test_sinusoid_quarter_period():
    # sin(π/2) = 1, so at t=period/4 → offset + amplitude
    assert sinusoid(2.5, period_s=10.0, amplitude=2.0, offset=5.0) == pytest.approx(7.0)


def test_random_walk_is_deterministic_given_seed():
    rw_a = random_walk(seed=42, step_std=1.0, start=0.0)
    rw_b = random_walk(seed=42, step_std=1.0, start=0.0)
    seq_a = [rw_a() for _ in range(100)]
    seq_b = [rw_b() for _ in range(100)]
    assert seq_a == seq_b


def test_random_walk_respects_bounds():
    rw = random_walk(seed=7, step_std=10.0, start=0.0, min_val=-1.0, max_val=1.0)
    for _ in range(1000):
        v = rw()
        assert -1.0 <= v <= 1.0


def test_step_function():
    s = step(at_t=10.0, before=0.0, after=100.0)
    assert s(0.0) == 0.0
    assert s(9.999) == 0.0
    assert s(10.0) == 100.0
    assert s(50.0) == 100.0


def test_burst_function():
    b = burst(at_t=5.0, duration_s=2.0, magnitude=3.0)
    assert b(4.999) == 0.0
    assert b(5.0) == 3.0
    assert b(6.999) == 3.0
    assert b(7.0) == 0.0


def test_jitter_is_deterministic_per_t():
    j = jitter(seed=11, std=1.0)
    assert j(1.0) == j(1.0)  # cached
    # different t → different value (almost certainly, with overwhelming probability)
    assert j(1.0) != j(2.0)
