from app.core.db import engine


def test_suite_never_binds_to_developer_database():
    assert engine.url.get_backend_name() == "sqlite"
    assert "/tests/_test_" in str(engine.url.database)
