"""Waiting for something another thread does, in tests.

The HTTP server writes its access line after the answer has gone out, so a test that reads the log right
after the response can be early on a slow machine.
"""
import time


def wait_until(check, timeout=5.0, step=0.01):
    """`check()` as soon as it is true, polled until `timeout` seconds have passed; its last value either way."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(step)
