import ingestion


def test_ingestion_package_imports():
    assert ingestion.__name__ == "ingestion"
