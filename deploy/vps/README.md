# my-vps nightly reflector job

These files are the source of truth for the nightly reflector-directory build
that runs on my-vps (`root@74.208.108.191`). The copies on the box are installed
from here.

## What runs

A systemd timer fires at 05:30 UTC (plus up to 20 minutes of random delay,
`Persistent=true`). It starts the oneshot service, which runs
`/opt/hamcall-db/build.sh`. That script pulls `origin/main`, builds the reflector
directory, runs `verify.py`, then builds the AllStar directory as a separate
step, and commits and pushes only if the output changed.

The AllStar step (`hamcall-db-allstar`, checked by `verify_allstar.py`) is
isolated from the reflectors. If the download fails, the directory is too small,
or the check fails, `build.sh` restores or removes `allstar.json` and carries on,
so only `allstar.json` is skipped that night and the reflectors still publish.

It runs on this box because DVRef sits behind Cloudflare, which refuses GitHub
runner IP ranges. `build.sh` explains this in full.

## Layout of /opt/hamcall-db on the box

- `build.sh` - the script the service runs (copied from this directory)
- `env` - mode 600, holds `DVREF_API_TOKEN`; never committed
- `repo/` - the clone the script builds in and pushes from
- `uv-cache/` - uv's cache (the service sandbox keeps `/root` read-only)

## Installing changes

```
ssh root@74.208.108.191 'install -m 700 /opt/hamcall-db/repo/deploy/vps/build.sh /opt/hamcall-db/build.sh && install -m 644 /opt/hamcall-db/repo/deploy/vps/hamcall-db-reflectors.{service,timer} /etc/systemd/system/ && systemctl daemon-reload'
```

Only `build.sh` and the systemd units are installed. `build.sh` is copied out of
the repo because it runs `git reset --hard` on that repo, which would rewrite it
while it runs. The two Python scripts, `verify.py` and `verify_allstar.py`, run
straight from the repo after the reset (`uv run python deploy/vps/verify.py`), so
a change to either takes effect on the next run without reinstalling. The old box
copy `/opt/hamcall-db/verify.py` is no longer used and can be deleted once the new
job has run once.
