# Rate limits Across Multiple Python3 Processes (Interpreters)

If a script can be called many times (for instance if every web request
invokes a new Python interpreter (you use CGI and not WSGI), by multiple cron
jobs, user initiated jobs, or whatever) and you want to rate limit something
(like sending yourself email or calling an external API within the specified
limits) across all invocations of the Python interpreter (processes) then
using existing rate limit libraries will not work since they only rate limit
among threads running within the same Python interpreter.

To do that you have to use something outside of Python, like the filesystem,
the OS kernel, or a database, etc.

This class uses the filesystem.

All calls are _serialized_ which isn't the best if each call normally takes a
long time relative to the interval time or you need many concurrent calls,
but, it is perfectly fine if in the normal case you are well below the rate
limits and it has nice properties when things are overloaded (see "Being Nice
to External API Services" below).

## Motivation

There are a few rate limit libraries I found (looking in pypi) but the only
ones that would work across processes use Redis (which isn't bad, but I didn't
really want to install and figure out how to maintain an whole new program
(with all its security considerations) and learn about all of it, just to have
rate limits), so I wrote this.

If I missed one that does work across processes, *LET ME KNOW* (in issues).

My use case (many separately invoked scripts (each with their own Python
interpreter)) is likely not as common as the ones that are a front-end to
something like Redis, use a large library (like Twisted), or are embedded in a
web-server using WSGI (but NOT CGI, which does run a new interpreter on each
request so the existing libraries won't work there either).  So, if you are
outside of those kinds of situations, then this may be useful to you.

## Implementation Notes

This implementation uses a Sqlite DB file to persist timing information and
synchronize access.  Sqlite is built into Python, so nothing should need to be
installed and it should just work.

### Timing

The timing is done with `time.time()`, which unfortunately means we may break
the rate limits if the user changes the OS time.  If the time is
discontinuously set forward then we may double the allowed calls within
whatever time frame (and a malicious user could set the time forward more than
the wait interval every time a call is made, which would effectively defeat
the rate limits).  If the time is set backwards then this will wait no more
than the maximum seconds for that particular rate limit (if we didn't check
for this the calls would wait until the time returned to whatever time it was
previously set to).  Since the time is slewed by NTP (and Unix time has no
daylight savings time) the only changes we need to worry about are the ones
the user makes to the operating system clock, which should happen rarely (and
if there is a malicious user on your OS that can change the time you have
bigger problems).

We can't use `time.monotonic()` because it is not guaranteed to use the same
reference across different Python processes.  It also has the problem that
since the DB file may persist (or not, we don't know) over a reboot, and the
reference time is unknown and probably different we may end up with the same
time change problems as discussed above with `time.time()`.

### Database

Using the filesystem is actually much harder than it sounds, so I used an
Sqlite DB to meter (which takes care of all the subtle file-locking problems,
and gives us a place to persist values across invocations).  The developers of
Sqlite have a deep understanding of the locking and persisting problems
(inconsistencies) of different operating systems and file systems.  (If you
think that is a solved problem, search around the web and read about the many
ways `fsync` is inconsistent and broken.)

If the DB file doesn't exist it is created (though any parent directories are
not) and this script is written so that there are no issues if the DB file
gets deleted at any time (the tables are created if they don't exist).  (Of
course if the DB is deleted after every API call then we can't maintain rate
limits.)

When the values of the rate limits are modified the DB file should be manually
deleted (which will also delete any user data stored there).  If instead we
tried to updated the DB when this is called then two versions of this program
could fight (changing the values in the DB back and forth forever) and break
both sets of rate limits.  To that end the rate limits passed into the class
constructor are only used to create the table and not used _at all_ when
enforcing the rate limits (they are read back out of the DB on every call).

### Being Nice to External API Services

The primary use case for me is to call external web API~s, and I am generally
way under the rate limits and want to be nice to them when things get
overloaded and backed up, so this library *DOES NOT* try to get the maximum
concurrency and use the maximum number of calls per interval.  Instead it will
always be way under them.

The time is recorded _after_ the call, and every call is completely
serialized, meaning that if you make some calls (that are still under the rate
limit) from different threads or processes they will happen one after the
other with no concurrency at all.

This also has the nice property that when whatever API is being
called into is overloaded we aren't being dicks by flooding it with more calls
as they are taking forever and piling on the the overloading (since we only
have one happening at any one time instead of what most libraries do, which is
every interval start the maximum allowed number of calls, which just backs up
the server more).  It also means that the effective rate limit will always be
_slower_ than the given limits.  (Since the time to make the call is counted
as part of the interval, so if the call time is very long (compared to the
interval) you may only get one call per interval.)

So, this library is only useful if, in the normal case, you are going to be
comfortably under the API rate limits, you don't need concurrent calls, the
call time is normally less than the interval and you want to be nice when the
calls are backed up and taking too long to return.  If you need to be right on
the line and/or want maximum concurrency, look into implementing a token
bucket.  (Or ping me, I might be convinced add an option to do that to this
library.)

### Why Not a Module?

This could be a module, but it isn't _that_ much code and I suspect people
will want to modify bits of it to actually use it, so I figure just a flat
file is better.  (Which you can just copy alongside your script and then
import it.)  If people want this to be a module, raise a GitHub issue, or send
me a pull request.

### Why Not Use Function Decorators

Function decorators can only be applied to the definition of a function, where
with this library you can just pass in any function you want.

## Usage and Examples

### Basic Usage

    import MultiProcRateLimit

This is the Sqlite DB file and it needs to be the same for every
interpreter/process/thread that is rate limited together.

    dbfile = "/tmp/ratelimits.sqlite"

This is a list or tuple of rate limits, each of which is `(rate,
per_interval)`, where rate is an int and per_interval is a float in seconds.
This example is no more calls than once per second, and no more than 75 times
every hour (which is 3600 seconds).  Remember that these rate limits are
_only_ used in the constructor _if_ the Sqlite DB file is created.  If it
already exists then the given values are thrown away and later on every call
the values are read back out of the DB.

    ratelimits = (
            (1, 60.0),  # Allow one call per second.
            (75, 3600.0),  # And also only allow 75 calls per hour.
        )

Instantiate the class (the instance can be passed to threads, or each thread
can instantiate their own).  This will almost never raise any exceptions, even
if, for instance, the DB file returns a "permission denied".  This is because
if the DB exists and is readable the table create will fail and raise (which
isn't a problem at all).  So to find out if there is a filesystem error you
need to try and call it with a function you want to rate limit, which will
raise an exception if something doesn't work.

    ratelimit = MultiProcRateLimit.MultiProcRateLimit(dbfile, ratelimits)

To rate limit the call to `myfunc(arg1, arg2)` we do:

    ratelimit.call(myfunc, (arg1, arg2))

Additionally there is a function to directly query the DB (the Sqlite DB file),
which can be called to allow the program to store a little bit of data:

    def db_create_table_fn(conn):
        conn.execute(
            "CREATE TABLE my_table ("
                "id INTEGER PRIMARY KEY, "
                "some_numbers REAL)"
            )

    def db_store_number(conn, id_int, n_float):
        conn.execute(
            "INSERT INTO my_table (id, some_numbers) "
                "VALUES (%d, %f)" % (id_int, n_float)
            )

    def db_get_all_numbers(conn):
        return conn.execute("SELECT id, some_numbers FROM my_table").fetchall()

    ratelimit.isolate_db_query(db_create_table_fn, ())

    ratelimit.isolate_db_query(db_store_number, (0, 2.2))
    ratelimit.isolate_db_query(db_store_number, (1, -52342.038))
    ratelimit.isolate_db_query(db_store_number, (2, 0.0))

    nums = ratelimit.isolate_db_query(db_get_all_numbers, ())
    for n in nums:
        print(n)

    # Prints:
    # (0, 2.2)
    # (1, -52342.038)
    # (2, 0.0)

_Remember_ that this DB access is _serialized_ with the calls that are rate
limited (even if the direct DB access is not rate limited), so it would be
possible, if you are really doing a lot of DB access, to slow down the calls
that need to be rate limited.  This DB access function is just here because it
is easy to include and is useful for many scripts that just want to share a
tiny bit of data between threads or processes and don't want to do their own
concurrency checks (locks and semaphores, etc.).
