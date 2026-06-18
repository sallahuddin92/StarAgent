# SQLite Overview

SQLite is a self-contained, serverless, zero-configuration SQL database engine. It is embedded directly into the application, requiring no separate database server process.

## Key Characteristics

- **Serverless**: No separate server process. The database is a single file on disk.
- **Zero Configuration**: No setup or administration required.
- **Lightweight**: Library size under 600KB.
- **ACID Compliant**: Transactions are atomic, consistent, isolated, and durable.
- **Single Writer**: Only one writer can modify the database at a time. Concurrent reads are supported.
- **Storage**: Data stored in a single `.db` file. Easy to backup by copying the file.
- **Performance**: Fast for read-heavy workloads and small to medium data sizes (up to ~140TB theoretical limit).
- **Concurrency**: Limited concurrent write performance. Best suited for single-server applications with low write concurrency.

## Best For

- Embedded applications (mobile, desktop)
- Small to medium web applications (low concurrency)
- Development and testing
- Data analysis and prototypes
- Read-heavy workloads

## Limitations

- No network access (not a client-server database)
- Limited concurrency for writes
- No built-in replication
- No user management or access control
- Less suitable for high-volume, multi-writer scenarios
