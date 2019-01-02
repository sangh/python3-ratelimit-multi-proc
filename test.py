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

dbfile = "/tmp/MultiProcRateLimit.sqlite"
lims = (( 1,  0.5),  # 2 times a second.
        (20, 10.0),  # 20 times in 10 seconds.
        (50, 30.0),  # 50 times in half a minute.
    )

try:
    os.remove(dbfile)  # If left over from previous runs.
except Exception as exp:
    pass

ratelimit = MultiProcRateLimit.MultiProcRateLimit(dbfile, lims)

def start_thread(n_threads):
    """Start `n_threads`, each with 4 calls."""
    def t_worker(*args, **kwargs):
        global ratelimit, dbfile, lims

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
            tds.append(executor.submit(t_worker, (), {}))
        for t in tds:
            _ = t.result()

def start_proc(script_name, num_procs):
    """Should return 8 "." characters for each proc."""
    # So this is confusing.  Subprocess won't actually start and run another
    # thread on Popen unless nothing uses the output, but we need the output,
    # so we are forced to call subprocess _in_a_thread_ (which then that
    # thread starts another process which itself has 2 threads).
    # We won't use concurrent.futures.ProcessPoolExecutor b/c no retcode.
    def one_proc(*args, **kwargs):
        proc = subprocess.run([script_name, "start_thread"],
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
            tds.append(executor.submit(one_proc, (), {}))
        for t in tds:
            rets.append(t.result())

    assert(len(rets) == num_procs)
    return rets


def test_8(script_name):
    wrn("Run of 8 calls which should take a bit " + \
        "more than 3.5 seconds.")
    time_start = time.time()
    ret = start_proc(script_name, 1)  # Should be here since in __main__.
    time_dur = time.time() - time_start
    cnt = 0
    for c in "".join(ret):
        if c == ".":
            cnt = cnt + 1
        else:
            wrn("Invalid char returned: %s" % (ret, ))
    wrn("Count: %d  took %f seconds." % (cnt, time_dur))
    assert(cnt == 8)
    assert(time_dur > 3.5)

def test_24(script_name):
    wrn("Run of 24 calls which should take a bit " + \
        "more than 11.5 seconds.")
    time_start = time.time()
    ret = start_proc(script_name, 3)  # Should be here since in __main__.
    time_dur = time.time() - time_start
    cnt = 0
    for c in "".join(ret):
        if c == ".":
            cnt = cnt + 1
        else:
            wrn("Invalid char returned: %s" % (ret, ))
    wrn("Count: %d  took %f seconds." % (cnt, time_dur))
    assert(cnt == 24)
    assert(time_dur > 11.5)

def test_56(script_name):
    wrn("Run of 56 calls which should take a bit " + \
        "more than 32.5 seconds.")
    time_start = time.time()
    ret = start_proc(script_name, 7)  # Should be here since in __main__.
    time_dur = time.time() - time_start
    cnt = 0
    for c in "".join(ret):
        if c == ".":
            cnt = cnt + 1
        else:
            wrn("Invalid char returned: %s" % (ret, ))
    wrn("Count: %d  took %f seconds." % (cnt, time_dur))
    assert(cnt == 56)
    assert(time_dur > 32.5)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # No arguments.
        test_8(sys.argv[0])
        test_24(sys.argv[0])
        test_56(sys.argv[0])
        wrn("Passed")
    elif len(sys.argv) == 2:
        if sys.argv[1] == "start_thread":
            start_thread(2)
        else:
            bye("Invalid 2nd argument.")
    else:
        bye("Too many arguments.")
