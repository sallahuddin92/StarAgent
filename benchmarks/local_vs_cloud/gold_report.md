# Deep Research: What are the trade-offs between local SQLite and cloud PostgreSQL for a small web application?

## Summary
SQLite and PostgreSQL serve different use cases in the database ecosystem. SQLite excels in simplicity and embedded scenarios, while PostgreSQL offers superior concurrency and advanced features at the cost of operational overhead.

## Key Findings
- SQLite is serverless and requires zero configuration, making it ideal for embedded applications [S1].
- PostgreSQL uses a client-server architecture and supports many concurrent writers [S2].
- SQLite is limited to a single writer, while PostgreSQL handles high concurrency efficiently [S3].

## Evidence Table
| Evidence ID | Source | Relevance | Quote | Assertion |
|---|---|---|---|---|
| [E1] | S1 | 1.0 | SQLite is a self-contained, serverless, zero-configuration SQL database engine | SQLite is serverless and zero-configuration |
| [E2] | S2 | 1.0 | PostgreSQL uses a client-server architecture with a separate server process | PostgreSQL uses client-server architecture |

## Limitations
Analysis is based on documented characteristics of each database system.

## Citations
- [E1]: direct quote from S1
- [E2]: direct quote from S2

## Source List
- [S1]: SQLite Overview
- [S2]: PostgreSQL Overview
- [S3]: Comparison Notes
