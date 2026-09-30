CREATE SCHEMA IF NOT EXISTS public;

CREATE TABLE IF NOT EXISTS public.committee_members (
    FirstName VARCHAR,
    Surname VARCHAR,
    CID VARCHAR,
    Email VARCHAR,
    Login VARCHAR,
    PostName VARCHAR,
    PhoneNo VARCHAR,
    StartDate TIMESTAMP
)