# Rate limits Across Multiple Python3 Processes (Interpreters)

If a script can be called many times (for instance from every web request or
by multiple cron jobs or whatever) and you want to rate limit something (like
sending yourself email or calling an external API within the specified limits)
across all invocations of the python interpreter (processes) then using most
of existing rate limit libraries will not work since they only rate limit
among threads running within the same Python interpreter (process).

You have to use something outside of Python, like the filesystem or kernel.

## Motivation

There are a few rate limit libraries I found (looking in pypi) but the only
ones that would work across processes use Redis (which isn't bad, but I didn't
really want to install and figure out how to maintain an instance (with
security considerations) and learn about all that just to have rate limits).
So I wrote this.

If I missed one that does all this, LET ME KNOW (in issues).

## Implementation Notes

### Timing
The timing is done with `time.time()`, which unfortunately means we may break
the limits when the system time is changed.  If the time is discontinuously
set forward then we may double the allowed calls within whatever time frame
(and if the time is set forward more than the interval every time a call is
made effectively no rate limits would be in effect).  If the time is set
backwards then this will wait no more than the seconds in whichever interval
(if we didn't check for this the calls would wait until the time returned to
whatever time it was previously set to).  Since the time is slewed by NTP (and
Unix time has no daylight savings time) the only changes we need to worry
about are the ones the user makes, which should be rare.

Since `time.monotonic()` is not guaranteed to be the same across different python
processes, we can't use it.  It also has another problem in that since the DB
file may persist (or not, we don't know) over a reboot, and reference is
unknown we may end up with the same time change problems discussed above with
`time.time()`.

The time is recorded _after_ the call, and every call is completely
serialized, meaning that if you make some calls (that are still under the rate
limit) they will happen one after the other with no concurrency at all.  It
also means that when whatever API is being callend into is overloaded we
aren't being dicks by flooding it with more calls as they are timing out
(since we only have one happening at any one time).  It also means that they
will always be _slower_ than the given limits.  (For instance, if it is
limited to 20 times an hour then over many hours we will, in the worst case,
actually only allow 20 times per hour plus the total call time all the calls
took.)

So, this library is only useful if, in the normal case, you are going to be
comfortably under the API rate limits.  If you need to be right on the line
and/or want maximum concurrency, look into implementing a token bucket.


### Database
Using the filesystem is actually much harder than it sounds, so I used an
Sqlite DB to meter (which takes care of all the subtle file-locking problems,
and gives us a place to persist values across invocations).  The developers of
Sqlite have a deep understanding of the locking and persisting problems
(inconsistencies) of different operating systems and file systems.  (If you
think that is a solved problem, search around the web and read about the
many ways `fsync` is inconsistent and broken.)

If the DB file doesn't exist it is created (though any parent directories are
not) and this script is written so that there are no issues if the DB file
gets deleted at any time (the tables are created if they don't exist).  (Of
course if the DB is deleted after every API call then we can't maintain rate
limits.)

When the values of the rate limits are modified the DB file should be manually
deleted (which will also delete any user data stored there).  If instead we
tried to updated the DB then two versions of this program could fight
(changing the values in the DB back and forth forever) and break both sets of
rate limits.  To that end the rate limits passed into the call constructor are
only used to create the table and not at all when using the DB to enforce them
(they are read back out of the DB).

## Why Not a Module?

This could be a module, but it isn't _that_ much code and I suspect people
will want to modify bits of it to actually use it, so I figure just a flat
file is better.  (Which you can just copy alongside your script and import
it.)  If people want to use this as a module, raise a GitHub issue or send
me a pull request.

## Usage

## Examples
