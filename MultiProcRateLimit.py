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
        dbfile = "/tmp/ratelimits.sqlite",

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

        # To rate limit the call to `myfunc(arg1, arg2)` we do:
        ratelimit.call(myfunc, (arg1, arg2))

    """
    def __init__(self, db_filename, create_db_with_these_ratelimits):
        """See class documentation.
        
        This will almost never raise an exception.  You need to call the
        `call` function to see if you get filesystem errors.

        If the DB file is missing sqlite will create it.  Then we try and
        create the tables.  If the table exists this will raise an exception
        and the insert clause will never be executed (but that exception will
        not be propagated back outside this function).  We could check if the
        table exists with:

        SELECT name
            FROM sqlite_master
            WHERE type='table'
                AND name='table_name_to_check_if_exists'
            COLLATE NOCASE

        and then do the insert, but that didn't measure much faster on my
        machine and there are other errors we also want to swallow.

        Anyway, if anything fails then the whole of it will be rolled back.
        """
        self.db_filename = db_filename

        def create_query(
                conn,
                format_unix_time_float_for_db,
                create_db_with_these_ratelimits):

            tnow = format_unix_time_float_for_db()

            conn.execute(
                "CREATE TABLE ratelimit_last_query_time ("
                    "id INTEGER PRIMARY KEY CHECK (id = 0), "
                    "unix_time_float REAL"
                ")")
            conn.execute(
                "INSERT INTO ratelimit_last_query_time "
                    "(id, unix_time_float) "
                    "VALUES (0, %s)" % tnow
                )
            conn.execute(
                "CREATE TABLE ratelimit_ratelimits ("
                    "id INTEGER PRIMARY KEY, "
                    "allowed_times_int INTEGER, "
                    "per_seconds_float REAL, "
                    "count_int INTEGER, "
                    "since_float REAL"
                ")")
            idx = 0
            for rl in create_db_with_these_ratelimits:
                conn.execute(
                    "INSERT INTO ratelimit_ratelimits "
                        "(id, allowed_times_int, per_seconds_float, "
                        "count_int, since_float) "
                        "VALUES (%d, %d, %f, 0, %s)" % (idx, rl[0], rl[1], tnow)
                    )
                idx = idx + 1

        try:
            self.isolate_db_query(create_query, (
                format_unix_time_float_for_db,
                create_db_with_these_ratelimits))
        except Exception as exp:
            pass


    def call(self, query_fn, query_fn_args):
        """Main function that should be called to ratelimit the given
        `query_fn`.  This queries and sets the last query time and then waits
        (by sleeping) for the ratelimits to allow the call.
        
        This function returns whatever `query_fn(*query_fn_args)` returns.
        """

        def wait_call_query_fn(
                conn,
                format_unix_time_float_for_db,
                query_fn,
                query_fn_args):

            last_query_time = conn.execute(
                "SELECT unix_time_float "
                    "FROM ratelimit_last_query_time "
                    "WHERE id = 0"
                ).fetchall()[0][0]

            ratelimits = conn.execute(
                "SELECT id, allowed_times_int, per_seconds_float, "
                    "count_int, since_float "
                    "FROM ratelimit_ratelimits"
                ).fetchall()

            rl_ids_to_update = []
            for ratelimit in ratelimits:
                rl_id, allowed_times_int, per_seconds_float, \
                        count_int, since_float = ratelimit

                if count_int < allowed_times_int:
                    conn.execute(
                        "UPDATE ratelimit_ratelimits SET count_int = "
                        "%d WHERE id = %d" % (count_int + 1, rl_id)
                        )
                else:
                    rl_ids_to_update.append(rl_id)
                    # Cap sleep time to `per_seconds_float` in case the user
                    # changed the OS clock way forward to avoid waiting however
                    # long that is because the max we should ever wait to be
                    # within the raet limit is `per_seconds_float`.
                    wait_time = \
                            last_query_time + per_seconds_float - time.time()
                    if wait_time > 0:
                        if wait_time > per_seconds_float:
                            wait_time = per_seconds_float
                        time.sleep(wait_time)

            ret = query_fn(*query_fn_args)

            tfinished = format_unix_time_float_for_db()

            conn.execute(
                "UPDATE ratelimit_last_query_time "
                    "SET unix_time_float = %s WHERE id = 0" % tfinished
                )
            conn.execute(
                "UPDATE ratelimit_ratelimits SET count_int = 1, since_float = "
                    "%s WHERE id IN (%s)" % (
                        tfinished,
                        ", ".join(map(lambda x: str(x), rl_ids_to_update))
                    )
                )

            return ret

        return self.isolate_db_query(
                wait_call_query_fn,
                (self.format_unix_time_float_for_db, query_fn, query_fn_args))


    def isolate_db_query(self, callback, callback_extra_args):
        """Run `callback(conn, *callback_extra_args)` within an exclusive
        lock on the connected Sqlite DB (accessed via `conn`).
        
        See the `README.md` file for some usage examples.
        
        Ridiculously the sqlite module doesn't do locking by default even
        though the docs kinda imply that `with conn...` is enough to do so.
        Instead you have to say the magic words AND you have to make sure you
        actually call commit or rollback to release the exclusive lock.
        
        So this function takes a function that takes the connection where
        whatever happens in the function is isolated.

        The callback doesn't need to (and should not) call commit, callback, or
        close.  If the callback returns cleanly then `commit()` is called, if
        it raises an exception then `rollback()` is called and the same
        exception is re-raised from this function.  In every case the
        connection is closed before returning (via the finally clause).
        
        The timeout is set to 25 hours, which may be longer than you want to
        wait before this returns a `sqlite3.OperationalError: database is
        locked`, assuming, of course, that you have so many waiting requests
        that the API takes that long to process them."""

        conn = sqlite3.connect(self.db_filename, 9000.0, 0, "EXCLUSIVE")
        try:
            conn.execute("BEGIN EXCLUSIVE")

            # This fn can call things like: ret = conn.execute(qs).fetchall()
            ret = callback(conn, *callback_extra_args)

            conn.commit()
            return ret
        except Exception as exp:
            try:
                conn.rollback()
            except Exception as exp:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception as exp:
                pass


    def format_unix_time_float_for_db(self):
        """We want to store the time (the float of unix-ish time returned by
        `time.time()`) with the maximum precision that is storable by the
        internal float representation.  There is no easy way to get this
        number, though in many cases Python prints out that representation by
        default so instead we use the max number of digits that Python
        guarantees that a string is convertible to a float and vice-versa with
        no loss of precision."""
        return "%%.%if" % sys.float_info.dig % time.time()
