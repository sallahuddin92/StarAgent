# Comparison Notes: SQLite vs PostgreSQL

## When to Choose SQLite
- Single-server or embedded applications
- Low write concurrency (under 10 concurrent writers)
- Simple backup strategy (file copy is sufficient)
- Development and prototyping
- The data fits on a single machine

## When to Choose PostgreSQL
- Multiple concurrent users writing data
- Need for advanced query capabilities
- Data needs to be replicated or highly available
- Complex access control requirements
- Large-scale applications expected to grow

## Trade-offs Summary

| Aspect | SQLite | PostgreSQL |
|--------|--------|------------|
| Setup | None | Requires installation and configuration |
| Concurrency | Single writer | Many concurrent writers |
| Management | File-based | Server-based |
| Features | Basic SQL | Advanced SQL + extensions |
| Scalability | Single machine | Horizontal with replication |
| Cost | Free, no hosting | Free, but hosting costs apply |
