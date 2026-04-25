"""Shared plane — one instance per process, serves all tenants.

Nothing in here may reference a tenant_id in its internal state. Tenant scope
is applied at call sites, not here.
"""
