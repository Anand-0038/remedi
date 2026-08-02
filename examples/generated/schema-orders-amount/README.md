# Remedi generated artifacts — `schema-orders-amount`

Merge-ready outputs + PR package from DataHub schemas, queries, and lineage.

Start with `PR_DESCRIPTION.md`, then review code files.

- `orders_amount_compat.sql` — Compat view for `amount` using DataHub schema types — grounded in `schema:amount, type:NUMBER(18,4), urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,retail.orders,PROD)`
- `schema_change_notice.md` — Owner-facing impact notice from DataHub lineage — grounded in `downstream:2`
- `PR_DESCRIPTION.md` — Merge-ready PR description for data team review — grounded in `urn:urn:li:dataset:(urn:li:dataPlatform:snowflake,retail.orders,PROD), downstream:2`
- `remedi.patch` — Git-apply-ready patch bundle of generated files — grounded in `artifacts:all`
