# Iceberg REST catalog fixture + PostgreSQL JDBC driver.
# The stock image only ships sqlite, which hits SQLITE_BUSY as soon as two writers
# (ingestor + transformer) commit at the same time. A Postgres-backed JdbcCatalog gives
# proper row-level optimistic concurrency for commits.
FROM apache/iceberg-rest-fixture:1.10.1
ADD --chown=iceberg:iceberg \
    --checksum=sha256:73914527305a40cce504b0d3d90b23caf565136912d607ae8ae7c5895512332c \
    https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.14/postgresql-42.7.14.jar \
    /usr/lib/iceberg-rest/postgresql.jar
CMD ["java", "-cp", "iceberg-rest-adapter.jar:postgresql.jar", "org.apache.iceberg.rest.RESTCatalogServer"]
