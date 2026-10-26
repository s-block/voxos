from voxos.db.runtime_readiness import RuntimeDatabaseReadiness


def readiness(**overrides: bool) -> RuntimeDatabaseReadiness:
    values = {
        "no_superuser": True,
        "no_create_role": True,
        "no_create_database": True,
        "no_replication": True,
        "no_bypass_rls": True,
        "cannot_assume_database_owner": True,
        "no_schema_create": True,
        "owns_no_application_tables": True,
        "cannot_update_alembic_version": True,
        "row_security_enabled": True,
    }
    values.update(overrides)
    return RuntimeDatabaseReadiness(**values)


def test_runtime_database_readiness_has_no_failures_when_restricted() -> None:
    assert readiness().failures() == ()


def test_runtime_database_readiness_reports_each_failed_control() -> None:
    result = readiness(
        no_bypass_rls=False,
        cannot_assume_database_owner=False,
        no_schema_create=False,
        cannot_update_alembic_version=False,
    )

    assert result.failures() == (
        "NOBYPASSRLS",
        "cannot assume the database owner role",
        "cannot create in the public schema",
        "cannot update alembic_version",
    )
