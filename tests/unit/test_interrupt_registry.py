"""Tests for ``agent.core.interrupt_registry`` (P12.1)."""

from __future__ import annotations

import asyncio

import pytest

from agent.core import interrupt_registry as ir


@pytest.fixture(autouse=True)
def _clean():
    ir.reset_all()
    yield
    ir.reset_all()


def test_acquire_creates_event_and_is_active():
    assert ir.is_active("c1") is False
    event = ir.acquire_event("c1")
    assert isinstance(event, asyncio.Event)
    assert event.is_set() is False
    assert ir.is_active("c1") is True


def test_release_drops_event():
    ir.acquire_event("c1")
    assert ir.is_active("c1") is True
    ir.release_event("c1")
    assert ir.is_active("c1") is False


def test_set_interrupt_signals_active_run():
    event = ir.acquire_event("c1")
    assert ir.set_interrupt("c1") is True
    assert event.is_set() is True


def test_set_interrupt_returns_false_for_no_active_run():
    assert ir.set_interrupt("nobody") is False


def test_acquire_clears_stale_signal_from_previous_run():
    event1 = ir.acquire_event("c1")
    ir.set_interrupt("c1")
    assert event1.is_set() is True
    # second acquire for the same conversation should reset the event so
    # the next run doesn't see a stale interrupt.
    event2 = ir.acquire_event("c1")
    assert event2 is event1  # same object reused
    assert event2.is_set() is False


def test_per_conversation_isolation():
    e1 = ir.acquire_event("c1")
    e2 = ir.acquire_event("c2")
    assert e1 is not e2
    ir.set_interrupt("c1")
    assert e1.is_set() is True
    assert e2.is_set() is False


def test_blank_conversation_id_maps_to_default_bucket():
    e1 = ir.acquire_event("")
    e2 = ir.acquire_event(None)
    e3 = ir.acquire_event("default")
    assert e1 is e2 is e3
