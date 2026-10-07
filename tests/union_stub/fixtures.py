"""Canned eActivities data for the union stub.

These are the raw PascalCase shapes the real union service (backend/union/main.py)
translates from — the stub sits where the real bridge sits, so this service does
the same translation to the snake_case rows the api consumes. Keeping the raw
shapes here means the translation code in main.py stays an honest copy of the
real one, and a change to it can be tested against the same fixtures.

The api's startup sync (SYNC_ON_STARTUP=true in docker-compose.test.yml) pulls
members immediately, so member1@ic.ac.uk resolves as a member on dev-login
without waiting for the nightly cycle. That is the whole point of this roster.
"""

# Members, one of whom the tests sign in as. A CID is required for a row to be
# usable, exactly as in the real feed — the bridge drops rows without one.
MEMBERS = [
    {
        "CID": "02689451",
        "FirstName": "Member",
        "Surname": "One",
        "Email": "member1@ic.ac.uk",
        "Login": "ab1234",
        "OrderNo": "ORD-100001",
        "MemberType": "Student Member",
    },
    {
        "CID": "02689452",
        "FirstName": "Member",
        "Surname": "Two",
        "Email": "member2@ic.ac.uk",
        "Login": "cd5678",
        "OrderNo": "ORD-100002",
        "MemberType": "Student Member",
    },
]

# A small committee roster with real post names, so admin screens that key on
# posts (cards, roles) have something shaped like July's handover to work with.
# Term dates follow the academic year the feed carries: 1 August to 31 July.
COMMITTEE = [
    {
        "CID": "02689461",
        "FirstName": "President",
        "Surname": "Example",
        "Email": "president@ic.ac.uk",
        "Login": "pr0001",
        "PostName": "President",
        "PhoneNo": "+44 20 7594 0000",
        "StartDate": "2026-08-01T00:00:00Z",
        "EndDate": "2027-07-31T23:59:59Z",
    },
    {
        "CID": "02689462",
        "FirstName": "Treasurer",
        "Surname": "Example",
        "Email": "treasurer@ic.ac.uk",
        "Login": "tr0002",
        "PostName": "Treasurer",
        "PhoneNo": "+44 20 7594 0001",
        "StartDate": "2026-08-01T00:00:00Z",
        "EndDate": "2027-07-31T23:59:59Z",
    },
    {
        "CID": "02689463",
        "FirstName": "Events",
        "Surname": "Example",
        "Email": "events@ic.ac.uk",
        "Login": "ev0003",
        "PostName": "Events Officer",
        "PhoneNo": "+44 20 7594 0002",
        "StartDate": "2026-08-01T00:00:00Z",
        "EndDate": "2027-07-31T23:59:59Z",
    },
]

# Two products, each with product lines — a line is what a sale references and
# what an admin links a ticket type to. Prices are strings because eActivities
# sends them as strings; the adapter parses them on the way into the mirror.
PRODUCTS = [
    {
        "ID": 501,
        "Name": "Annual Membership",
        "Description": "EESoc membership for the academic year.",
        "Active": True,
        "ProductLines": [
            {"ID": 5011, "Name": "Annual Membership", "Price": "5.00"},
        ],
    },
    {
        "ID": 502,
        "Name": "Winter Show",
        "Description": "Winter show tickets.",
        "Active": True,
        "ProductLines": [
            {"ID": 5021, "Name": "Standard Ticket", "Price": "8.00"},
            {"ID": 5022, "Name": "Member Ticket", "Price": "5.00"},
        ],
    },
]

# Empty by design: the sales feed is what confirms bookings were paid for, and
# starting from nothing means a test that needs a sale adds one deliberately.
SALES = []

# ICU's own event listing. Not used for booking (that is our `events` table),
# but the admin cross-check view reads it, so it is served empty rather than
# 404ing.
WHATSON = []
