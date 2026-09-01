"""The book - HealthCross's own already-booked membership and claims.

One uploaded dataset, shared by every workflow and owned by none of
them. New Business prices against it, Renewal reads each account's own
experience out of it, the Portfolio Analysis screen reports on it whole.

The rule that keeps the modules apart is that this package NEVER imports
a workflow. Nothing in here knows what a Case is, what a quote is, or
what a renewal is - it knows members, claims, premiums and dates. Every
dependency points inwards:

    book  <-  casework  <-  renewal
                        <-  newbusiness

Before this package existed, the book's data access lived inside the
Portfolio Analysis ROUTER, and six other modules reached into it for
private helpers - fourteen of those sixteen imports written inside a
function body to dodge the circular import that a module-level one would
have caused. A router is a screen's API; it is not a place to keep the
data layer, and the function-local imports were the code saying so.

  repository.py  what is in the book - rows out of the database
  analysis.py    what the book means - pricing, loss ratios, the cube
"""
