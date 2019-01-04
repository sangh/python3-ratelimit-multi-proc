#!/usr/local/bin/python3

# This is worefully incomplete, but I did use it to test the overall
# functionality, so I'm including it.

import os, sys, time
import subprocess
from concurrent.futures import ThreadPoolExecutor
import MultiProcRateLimit

def wrn(s):
    print(s, file=sys.stderr)

def bye(s):
    global wrn
    wrn(s)
    sys.exit(1)

def start_threads(n_threads):
    """Start `n_threads`, each with 4 calls, two with the global instance,
    and 2 with a locally created instance."""
    ratelimit = MultiProcRateLimit.MultiProcRateLimit(dbfile, lims)

    def t_worker(*args, **kwargs):
        global dbfile, lims
        nonlocal ratelimit

        new_rl_inst = MultiProcRateLimit.MultiProcRateLimit(dbfile, lims)

        def w():
            print(".", end='')

        ratelimit.call(w)
        new_rl_inst.call(w)
        ratelimit.call(w)
        new_rl_inst.call(w)

    with ThreadPoolExecutor(max_workers=n_threads) as executor:
        tds = []
        for _ in range(n_threads):
            tds.append(executor.submit(t_worker, *(), **{}))
        for t in tds:
            _ = t.result()


def start_proc(script_name, num_procs, num_threads):
    """Procs should return a "." characters for each call.  There are 4 calls
    per thread, and num_threads per proc, and num_procs total.  So the number
    of "." chars returned should be 4*num_threads*num_procs.
    
    Then this function should return the count of dots and the time taken to
    run all of the procs."""
    tstarted = time.time()
    # So this is confusing.  Subprocess won't actually start and run another
    # thread on Popen unless nothing uses the output, but we need the output,
    # so we are forced to call subprocess _in_a_thread_ (which then that
    # thread starts another process which itself has 2 threads).
    # We won't use concurrent.futures.ProcessPoolExecutor b/c no retcode.
    def one_proc(*args, **kwargs):
        proc = subprocess.run([script_name, "start_threads", kwargs["nt"]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        if proc.returncode != 0 or b'' != proc.stderr:
            bye("Error running subprocess: %d: %s" % (
                proc.returncode,
                proc.stderr.decode("utf-8", "backslashreplace")))
        return proc.stdout.decode("utf-8", "backslashreplace")

    rets = []
    with ThreadPoolExecutor(max_workers=num_procs) as executor:
        tds = []
        for _ in range(num_procs):
            kwargs = {"nt": "%d" % (num_threads, ), }
            tds.append(executor.submit(one_proc, *(), **kwargs))
        for t in tds:
            rets.append(t.result())

    assert(len(rets) == num_procs)

    ndots = 0
    for c in "".join(rets):
        if c == ".":
            ndots = ndots + 1
        else:
            wrn("Invalid char returned: %s" % (rets, ))

    return ndots, time.time()-tstarted


def test_8(script_name):
    wrn("Run 8 calls, should take a bit more than 3.5 seconds.")
    ndots, time_taken = start_proc(script_name, 1, 2)  # 8=1*2*4
    wrn("Count: %d  took %f seconds." % (ndots, time_taken, ))
    assert(ndots == 8)
    assert(time_taken > 3.5)

def test_24(script_name):
    wrn("Run 24 calls, should take a bit more than 11.5 seconds.")
    ndots, time_taken = start_proc(script_name, 3, 2)  # 24=3*2*4
    wrn("Count: %d  took %f seconds." % (ndots, time_taken, ))
    assert(ndots == 24)
    assert(time_taken > 11.5)

def test_56(script_name):
    wrn("Run 56 calls, should take a bit more than 32.5 seconds.")
    ndots, time_taken = start_proc(script_name, 7, 2)  # 56=7*2*4
    wrn("Count: %d  took %f seconds." % (ndots, time_taken, ))
    assert(ndots == 56)
    assert(time_taken > 32.5)





dbfile = "/tmp/MultiProcRateLimit.sqlite"
lims = (( 1,  0.5),  # 2 times a second.
        (20, 10.0),  # 20 times in 10 seconds.
        (50, 30.0),  # 50 times in half a minute.
    )

def clear_and_instantiate_ratelimit():
    """We _have_ to clear the dbfile before each test (when no other procs or
    threads are still running) because if we don't we may end up starting the
    next test before the interval (time window where x of however many calls
    are allowed) is over.  If that happens then the first few of the new test
    run will fall within the previous window, which if of course perfectly
    fine if this is being used, but it will make the test run take less time
    than it should (and we check for)."""
    global dbfile
    for f in (dbfile, dbfile + "-journal"):
        if os.path.exists(f):
            os.remove(f)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        clear_and_instantiate_ratelimit()
        test_8(sys.argv[0])

        clear_and_instantiate_ratelimit()
        test_24(sys.argv[0])

        clear_and_instantiate_ratelimit()
        test_56(sys.argv[0])
        wrn("Passed")
    elif len(sys.argv) == 3:
        if sys.argv[1] == "start_threads":
            start_threads(int(sys.argv[2]))
        else:
            bye("Invalid 2nd argument.")
    else:
        bye("Incorrect arguments.")
