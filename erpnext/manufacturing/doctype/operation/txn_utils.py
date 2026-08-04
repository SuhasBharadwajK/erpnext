# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Transaction helpers for the factory-floor station endpoints.

The station endpoints (start/finish process, queue moves, QC, etc.) each
mutate several documents in sequence (time log -> job card -> work order ->
stock entry -> slab). Historically these ran without an enclosing transaction
boundary and sprinkled intermediate ``frappe.db.commit()`` calls, so a failure
midway (very often a ``QueryDeadlockError`` under concurrent load) left a
half-done state behind and the per-fragment deadlock retries were ineffective.

``run_atomic`` makes a whole endpoint a single unit of work:

* it commits exactly once, on success;
* on ``QueryDeadlockError`` it rolls the *entire* operation back and retries it
  with exponential backoff, so transient lock cycles become invisible;
* on any other exception it rolls back and re-raises, so no partial state
  survives.

It is **reentrant**: if a wrapped endpoint calls another wrapped endpoint, the
inner call runs inline and the outermost call owns the commit/rollback/retry.
This lets every station entry point wrap its own body without breaking
atomicity when one orchestrates another (e.g. ``move_slab_iteratively_to``
driving ``finish_process``).
"""

import time

import frappe
from frappe import QueryDeadlockError

_ACTIVE_FLAG = "in_run_atomic"

DEFAULT_RETRIES = 5
DEFAULT_BASE_DELAY = 0.3


def run_atomic(fn, *args, retries: int = DEFAULT_RETRIES, base_delay: float = DEFAULT_BASE_DELAY, **kwargs):
	"""Run ``fn(*args, **kwargs)`` as a single transaction with whole-operation
	deadlock retry.

	If we are already inside a ``run_atomic`` block, ``fn`` is executed inline
	and the outermost block manages the transaction.
	"""
	if frappe.flags.get(_ACTIVE_FLAG):
		return fn(*args, **kwargs)

	for attempt in range(retries):
		frappe.flags[_ACTIVE_FLAG] = True
		try:
			result = fn(*args, **kwargs)
			frappe.db.commit()
			return result

		except QueryDeadlockError:
			frappe.db.rollback()
			if attempt == retries - 1:
				raise
			time.sleep(base_delay * (2**attempt))

		except Exception:
			frappe.db.rollback()
			raise

		finally:
			frappe.flags[_ACTIVE_FLAG] = False


def atomic_endpoint(fn=None, *, retries: int = DEFAULT_RETRIES, base_delay: float = DEFAULT_BASE_DELAY):
	"""Decorator form of :func:`run_atomic`.

	Usage::

		@frappe.whitelist()
		@atomic_endpoint
		def finish_process(...):
			...
	"""
	import functools

	def decorator(func):
		@functools.wraps(func)
		def wrapper(*args, **kwargs):
			return run_atomic(func, *args, retries=retries, base_delay=base_delay, **kwargs)

		return wrapper

	if fn is not None:
		return decorator(fn)
	return decorator


def lock_for_update(doctype: str, name: str | None):
	"""Acquire a row lock on a single document to serialise contended writers.

	Used to enforce a consistent lock-acquisition order (Production Plan ->
	Work Order -> Bin -> Workstation) across endpoints so concurrent stock
	postings serialise instead of deadlocking. No-op when ``name`` is falsy.
	"""
	if not name:
		return None
	return frappe.db.get_value(doctype, name, "name", for_update=True)
