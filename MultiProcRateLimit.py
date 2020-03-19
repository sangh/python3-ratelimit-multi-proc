#!/usr/local/bin/python3

# A general description, information about what this file is, how it works,
# and usage examples can be found in `README.md` which is distributed with
# this script or can be found online at:
#
#     https://github.com/sangh/python3-ratelimit-multi-proc/
#

import sys
import time
import sqlite3

class MultiProcRateLimit:
    """See https://github.com/sangh/python3-ratelimit-multi-proc/ for more
    information.  A quick example is:

        import MultiProcRateLimit

        # This is the Sqlite DB file and it needs to be the same for every
        # interpreter/process/thread that is rate limited together.
        dbfile = "/tmp/ratelimits.sqlite"

        # This is a set of rate limits (rate, per_interval) where rate
        # is an int and per_interval is a float in seconds.  This
        # is no more than once per second and no more than 75 times
        # every hour (3600 seconds).
        # Only used when the DB is created, after which they are read from it.
        ratelimits = (
            (1, 60.0),
            (75, 3600.0),
        )

        # Instantiate the class (the instance can be passed to threads, or
        # each thread can instantiate their own).
        ratelimit = MultiProcRateLimit.MultiProcRateLimit(dbfile, ratelimits)

        # To rate limit the call to `ret = myfunc()` we do:
        ret = ratelimit.call(myfunc)

        # To rate limit the call to `myfunc2(arg1, arg2, narg=318)` we do:
        ratelimit.call(myfunc2, (arg1, arg2), {"narg": 318, })

        # To rate limit the call to `ret = myfunc3(namedarg=somevalue)` we do:
        ret = ratelimit.call(myfunc3, (), {"namedarg": somevalue, })

    If `myfunc()` would have raised an exception, the call to
    `ratelimit.call(myfunc)` will raise the same exception.

    """
    def _isolate_db_query(self, query_fn, ret_lst, args, kwargs):
        """Helper function for `isolate_db_query(...)`.
        """
        conn = sqlite3.connect(
                self.db_filename,
                isolation_level=None,
                timeout=self.transaction_timeout)
        try:
            conn.execute("PRAGMA locking_mode=EXCLUSIVE;").close()
            conn.execute("BEGIN EXCLUSIVE;").close()

            # This fn can call things like: ret = conn.execute(qs).fetchall()
            # Remember that all cursors need to be closed!
            query_fn(conn, ret_lst, *args, **kwargs)

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def isolate_db_query(self, query_fn, args=(), kwargs={}):
        """Run `query_fn(conn, ret_lst, *args, **kwargrs)` within an exclusive
        lock on the connected Sqlite DB (accessed via `conn`).

        See the `README.md` file for some usage examples.

        Ridiculously the sqlite module doesn't do locking by default even
        though the docs kinda imply that `with conn...` is enough to do so.
        Instead you have to say the magic words AND you have to make sure you
        actually call commit or rollback to release the exclusive lock.
        
        So this function takes a function that takes the connection where
        whatever happens in the function is isolated.

        The query_fn *should not* call commit, callback, or close.  If the
        query_fn returns cleanly then `commit()` is called, if it raises an
        exception then `rollback()` is called and the same exception is
        re-raised from this function.  In every case the connection is closed
        before returning (via the finally clause).

        The query_fn should also not return anything (anything returned will
        be ignored), instead, if it wants it can appended one value to ret_lst.
        We do this b/c the function or the sqlite statements could throw and
        if sqlite throws after the function is called we do not want to call
        the function again.  This allows query_fn to have sqlite calls both
        before and after anything else it wants to do.

        That means that query_fn should _check_ if there are any values in
        ret_lst and not do the non-DB stuff if so.
        """
        acc_time = 0.0
        while True:
            t_start = time.time()
            try:
                ret = []
                self._isolate_db_query(query_fn, ret, args, kwargs)
                if ret:
                    return ret[0]
                else:
                    return
            except sqlite3.OperationalError:
                acc_time = acc_time + time.time() - t_start
                if acc_time > self.wait_timeout:
                    raise
            time.sleep(sys.float_info.epsilon)  # Really just need to yield.

    def __init__(self, db_filename, create_db_with_these_ratelimits):
        """See class documentation.

        If the DB file is missing sqlite will create it (or this function will
        raise an exception).  Then, if the table is not found using:

            SELECT name
                FROM sqlite_master
                WHERE type='table'
                    AND name='multi_proc_rate_limit'
                COLLATE NOCASE

        We will try to create and fill it.
        """
        if not isinstance(db_filename, str) and \
                not isinstance(db_filename, bytes):
            raise Exception("Argument db_filename is not a str or bytes.")

        # We try and open the DB right here in init.  This shouldn't block
        # ever if the connection works because we are not going call the
        # pragma for exclusive access, but this will fail (raising an
        # an exception if the file cann't be opened.  If we didn't check this
        # here then the client would have to wait for the self_wait timeout,
        # which is long, before getting an error back.  It is annoying that
        # sqlite returns the same Exception for permission denied (where
        # trying again is futile) and DB is locked (where we want to wait and
        # try again, maybe for a very long time).
        sqlite3.connect(db_filename, isolation_level=None, timeout=.01).close()

        for rl in create_db_with_these_ratelimits:
            if not isinstance(rl[0], int) or rl[0] <= 0:
                raise Exception("Rate must be an int greater than 0.")
            if not isinstance(rl[1], float) or rl[1] <= 0.0:
                raise Exception("Per_interval must be > 0.0 and a float.")

        self.db_filename = db_filename

        # This could be an argument, but since it is an int it must be less
        # than or equal to 2147483647 and greater than or equal to 0.
        # It is set to 2.5 hours, which may be longer than you want to
        # wait before this (or call or isolate) returns a:

        #   sqlite3.OperationalError: database is locked

        # Assuming, of course, that you have so many waiting requests (relative
        # to your ratelimits) that it takes that long to process them.
        self.wait_timeout = 9000.0  # 9k seconds is two and a half hours.
        self.transaction_timeout = 1  # Second.

        def mktable(conn, ret_lst, create_db_with_these_ratelimits):

            t_mktables_start = time.time()

            cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND "
                        "name='multi_proc_rate_limit' COLLATE NOCASE;"
                    )
            try:
                if cur.fetchall():
                    return
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

            conn.execute(
                "CREATE TABLE multi_proc_rate_limit ("
                    "id INTEGER PRIMARY KEY, "
                    "allowed_times_int INTEGER, "
                    "per_seconds_float REAL, "
                    "count_int INTEGER, "
                    "since_float REAL"
                ");").close()

            for idx, (allowed_times, per_secs) in enumerate(
                    create_db_with_these_ratelimits):
                # We want to set all the since_float times to long enough ago
                # that every interval, on the first call, gets reset to a count
                # of 1.  So we set it to longer than the interval (per second)
                # seconds ago and the count to maximum.
                conn.execute(
                    "INSERT INTO multi_proc_rate_limit "
                            "(id, "
                            "allowed_times_int, per_seconds_float, "
                            "count_int, since_float) "
                        "VALUES (%r, %r, %r, %r, %r);" %
                            (idx,
                            allowed_times, per_secs,
                            allowed_times, t_mktables_start - per_secs)
                    ).close()
            # End mktable.

        # Run create_query before returning from __init__.  This may raise.
        return self.isolate_db_query(
                mktable,
                args=(create_db_with_these_ratelimits, ))


    def call(self, query_fn, args=(), kwargs={}):
        """Main function that should be called to ratelimit the given
        `query_fn`.  This queries and sets the last query time and then waits
        (by sleeping) for the ratelimits to allow the call.
        
        This function returns whatever `query_fn(*args, **kwargs)` returns.
        """

        def wait_call(conn, ret_lst, query_fn, args, kwargs):

            time_called = time.time()

            cur = conn.execute(
                "SELECT id, allowed_times_int, per_seconds_float, "
                    "count_int, since_float "
                    "FROM multi_proc_rate_limit;"
                )
            ratelimits = cur.fetchall()
            cur.close()

            wait_time = 0.0
            ids_to_reset = []
            for rl_id, allowed_times, per_secs, cnt, t_since in ratelimits:

                if cnt < allowed_times and time_called < t_since + per_secs:
                    conn.execute(
                        "UPDATE multi_proc_rate_limit SET count_int = "
                        "%r WHERE id = %r;" % (cnt + 1, rl_id)
                        ).close()
                else:
                    ids_to_reset.append(rl_id)
                    # Cap sleep time to `per_secs` in case the user changed the
                    # OS clock way forward to avoid waiting however long that
                    # is because the max we should ever wait to be within the
                    # rate limit is `per_secs`.
                    new_wait_time = t_since + per_secs - time_called
                    if new_wait_time > per_secs:
                        new_wait_time = per_secs
                    wait_time = max(wait_time, new_wait_time)

            time.sleep(wait_time)

            if not ret_lst:
                ret_lst.append(query_fn(*args, **kwargs))

            conn.execute(
                "UPDATE multi_proc_rate_limit SET count_int = 1, since_float"
                    " = %r WHERE id IN (%s);" % (
                        time.time(),
                        ", ".join(map(lambda x: str(x), ids_to_reset))
                    )
                ).close()

            return

        # And call it.
        return self.isolate_db_query(wait_call, (query_fn, args, kwargs))

