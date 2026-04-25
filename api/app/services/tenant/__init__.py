"""Per-tenant services. One instance per active tenant, held in TenantRegistry.

All three (risk, execution, cycle) share a tenant_id; that's the invariant
the registry enforces.
"""
