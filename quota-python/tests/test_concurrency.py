"""No matter how many requests race, admitted estimates never exceed the limit."""

import threading

from tokenquota import Plan, Quota, QuotaExceeded


def hammer(q, threads=32, attempts=50, est=100):
    admitted = []
    lock = threading.Lock()

    def worker():
        for _ in range(attempts):
            try:
                r = q.reserve("shared-user", est_tokens=est)
            except QuotaExceeded:
                continue
            with lock:
                admitted.append(r)

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return admitted


def test_no_overspend_under_contention(store):
    q = Quota([Plan("p", tokens=10_000)], store=store, default_plan="p")
    admitted = hammer(q)
    assert len(admitted) == 100  # exactly limit / est
    for r in admitted:
        r.commit(input_tokens=100)
    s = q.status("shared-user")
    assert s.used == 10_000 and s.reserved == 0
