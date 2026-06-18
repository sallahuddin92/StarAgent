# PostgreSQL Overview

PostgreSQL is a powerful, open-source object-relational database system with over 30 years of active development. It uses a client-server architecture with a separate server process.

## Key Characteristics

- **Client-Server Architecture**: Runs as a separate server process, clients connect over the network.
- **Full ACID Compliance**: Strong transaction guarantees with MVCC (Multi-Version Concurrency Control).
- **High Concurrency**: Supports many concurrent writers and readers efficiently.
- **Advanced Features**: Full-text search, JSON/JSONB support, custom data types, indexing (B-tree, Hash, GiST, GIN, BRIN).
- **Replication**: Built-in streaming replication, logical replication, and hot standby.
- **Security**: Role-based access control, SSL support, row-level security.
- **Extensibility**: Custom functions, procedural languages (PL/pgSQL, PL/Python, PL/Perl), extensions (PostGIS, pgvector).
- **Performance**: Excellent for complex queries, large datasets, and high-write-throughput workloads.

## Best For

- Multi-user applications with concurrent writes
- Complex queries and reporting
- Large-scale web applications
- Data warehousing and analytics
- Geospatial applications (with PostGIS)
- Applications requiring advanced security features

## Limitations

- Higher operational overhead (server management, configuration)
- More resource-intensive than SQLite
- Requires network configuration
- Backup and restore more complex than file-based databases
- Overkill for simple, single-user applications
