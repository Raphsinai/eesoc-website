-- Cross-pod job leases: a row that says who is running a named job, and until
-- when nobody else may take it.
--
-- The guards on the daily ICU sync and on a bulk send used to be in-process
-- asyncio locks, which only ever worked while everything ran as one pod. A
-- rolling update starts the new pod before the old one stops, so for a minute
-- there are two processes that each believe they are the only one — and the
-- membership feed gets pulled, or a newsletter sent, twice.
--
-- A lease row outlives the pod that wrote it, which is the point: a pod that
-- dies mid-job leaves an expired row the next pod takes over, and a pod that
-- shuts down cleanly deletes its own. `expires_at` is the takeover path for
-- the pod that died without releasing — it must comfortably exceed the
-- longest job the lease guards (the sender allows fifteen minutes for a slow,
-- paced bulk send).
CREATE TABLE IF NOT EXISTS public.job_leases (
    name       VARCHAR     PRIMARY KEY,
    holder     VARCHAR     NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
